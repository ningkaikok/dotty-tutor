"""Model-backed lesson generation independent from HTTP and upload routes.

The functions in this module accept plain text/dicts and return serializable
payloads. Keeping them outside ``app.py`` makes model behavior testable without
constructing multipart requests or PDF upload jobs.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from model_runtime import runtime
from observability import log_event
from question_contracts import (
    CANVAS_ACTIONS,
    GUIDE_CARDS,
    LESSON_SCHEMA,
    LESSON_STEPS,
    QUESTION,
    HelpRequest,
    TutorReply,
)
from question_pipeline import (
    build_lesson_prompt,
    clean_question_stem,
    normalize_question_interaction,
    strip_choice_text_from_prompt,
)
from question_source import safe_string_list, safe_text, select_complete_question_source
from review_runtime import runtime_reviewer
from tutor_checks import mock_model_run
from tutor_engine import TutorEngine


# This cache accelerates the single-process demo. PostgreSQL remains the source
# of truth for persisted lessons; production multi-worker deployments should
# replace the cache with a shared store rather than synchronizing this dict.
lesson_store: dict[str, dict[str, Any]] = {}


def question_payload(
    question: dict[str, Any] | None = None,
    lesson_steps: list[dict[str, Any]] | None = None,
    model_run: dict[str, Any] | None = None,
) -> dict:
    """Build the stable frontend envelope used by live and Mock generation."""
    return {
        "question": question or QUESTION,
        "lessonSteps": lesson_steps or LESSON_STEPS,
        "architecture": {
            "input": "scanned textbook page",
            "stored": "question + answer + guide cards",
            "runtime": "student input + selected guide_context",
        },
        "modelRun": model_run or mock_model_run(),
    }


def _normalized_blanks(generated: dict[str, Any]) -> list[dict[str, Any]]:
    blanks: list[dict[str, Any]] = []
    if not isinstance(generated.get("blanks"), list):
        return blanks
    for index, raw_blank in enumerate(generated["blanks"][:8], start=1):
        if not isinstance(raw_blank, dict):
            continue
        try:
            tolerance = max(0.0, float(raw_blank.get("tolerance", 0) or 0))
        except (TypeError, ValueError):
            tolerance = 0.0
        blanks.append({
            "id": safe_text(raw_blank.get("id"), f"blank-{index}", 24),
            "label": safe_text(raw_blank.get("label"), f"第 {index} 空", 30),
            "answerType": safe_text(raw_blank.get("answerType"), "text", 20),
            "correctAnswers": safe_string_list(raw_blank.get("correctAnswers"), [], 6),
            "tolerance": tolerance,
            "unit": safe_text(raw_blank.get("unit"), "", 20),
        })
    return blanks


def _normalized_answer_spec(generated: dict[str, Any]) -> dict[str, Any] | None:
    raw = generated.get("answerSpec")
    if not isinstance(raw, dict):
        return None
    try:
        tolerance = max(0.0, float(raw.get("tolerance", 0) or 0))
    except (TypeError, ValueError):
        tolerance = 0.0
    return {
        "answerType": safe_text(raw.get("answerType"), "numeric", 20),
        "expected": safe_text(raw.get("expected"), "", 120),
        "accepted": safe_string_list(raw.get("accepted"), [], 6),
        "tolerance": tolerance,
        "unit": safe_text(raw.get("unit"), "", 20),
    }


def _normalized_steps(generated: dict[str, Any]) -> list[dict[str, Any]]:
    raw_steps = generated.get("lessonSteps") if isinstance(generated.get("lessonSteps"), list) else []
    steps: list[dict[str, Any]] = []
    for index in range(4):
        raw = raw_steps[index] if index < len(raw_steps) and isinstance(raw_steps[index], dict) else {}
        steps.append({
            "id": f"model-step-{index + 1}",
            "title": safe_text(raw.get("title"), f"第 {index + 1} 步", 80),
            "text": safe_text(raw.get("text"), "根据题目条件继续推理。", 700),
            "speechText": safe_text(raw.get("speechText"), "我们继续看下一步。", 700),
            "action": CANVAS_ACTIONS[index],
        })
    return steps


def _normalized_guide_cards(generated: dict[str, Any], knowledge_point: str) -> list[dict[str, Any]]:
    raw_cards = generated.get("guideCards") if isinstance(generated.get("guideCards"), list) else []
    cards: list[dict[str, Any]] = []
    for index in range(3):
        fallback = {
            "stuckAt": "需要把题目条件转化为下一步操作。",
            "knowledge": [knowledge_point],
            "hint": "先圈出题目明确给出的量，再判断当前能进行哪一步。",
            "question": "根据已知条件，你现在可以先写出什么关系？",
        }
        raw = raw_cards[index] if index < len(raw_cards) and isinstance(raw_cards[index], dict) else {}
        cards.append({
            "level": index,
            "stuckAt": safe_text(raw.get("stuckAt"), fallback["stuckAt"], 300),
            "knowledge": safe_string_list(raw.get("knowledge"), fallback["knowledge"]),
            "hint": safe_text(raw.get("hint"), fallback["hint"], 500),
            "question": safe_text(raw.get("question"), fallback["question"], 500),
            "canvasAction": CANVAS_ACTIONS[min(index + 1, 3)],
        })
    return cards


def generate_lesson(source_text: str) -> tuple[dict, list[dict[str, Any]], dict[str, Any]]:
    """Generate one validated-shape lesson, falling back to the demo seed."""
    selection = runtime.selection
    started = time.perf_counter()
    log_event("model.generation.started", provider=selection.provider, model=selection.model)
    source = source_text.strip()[:16_000]
    if not source:
        source = f"{QUESTION['prompt']}\n已知条件：{'；'.join(QUESTION['givens'])}"
    selected_number, selected_source, selected_images = select_complete_question_source(source)
    if selected_number:
        source = selected_source

    if selection.provider == "mock":
        run = mock_model_run()
        payload = question_payload(model_run=run)
        lesson_store[QUESTION["id"]] = {"payload": payload, "guideCards": GUIDE_CARDS}
        log_event("model.generation.completed", provider="mock", duration_ms=round((time.perf_counter() - started) * 1000, 1))
        return payload, GUIDE_CARDS, run

    try:
        generated, run = runtime.generate_json(build_lesson_prompt(source), LESSON_SCHEMA, max_tokens=1600)
    except Exception as error:
        run = mock_model_run(selection.provider, str(error))
        payload = question_payload(model_run=run)
        lesson_store[QUESTION["id"]] = {"payload": payload, "guideCards": GUIDE_CARDS}
        log_event(
            "model.generation.failed",
            level=40,
            provider=selection.provider,
            model=selection.model,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            fallback=True,
            error_type=type(error).__name__,
            error=str(error)[:300],
            exc_info=True,
        )
        return payload, GUIDE_CARDS, run

    question_id = f"generated-{hashlib.sha256(source.encode('utf-8')).hexdigest()[:12]}"
    question_type = safe_text(generated.get("questionType"), "short-answer", 30)
    if question_type not in {
        "choice", "multi-select", "true-false", "short-answer", "fill-blank", "numeric", "draw-line",
    }:
        question_type = "short-answer"
    givens = [
        item for item in safe_string_list(generated.get("givens"), [], 5)
        if "images/" not in item and "![" not in item
    ]
    options = safe_string_list(generated.get("options"), [], 6) if generated.get("options") else []
    prompt = clean_question_stem(selected_number, selected_source) if selected_number else safe_text(generated.get("prompt"), QUESTION["prompt"], 4000)
    knowledge_point = safe_text(generated.get("knowledgePoint"), "分步推理", 120)
    question = {
        "id": question_id,
        "questionType": question_type,
        "chapter": safe_text(generated.get("chapter"), "教材练习", 80),
        "knowledgePoint": knowledge_point,
        "questionNumber": selected_number or safe_text(generated.get("questionNumber"), "", 30),
        "prompt": strip_choice_text_from_prompt(prompt, options),
        "correctAnswer": safe_text(generated.get("correctAnswer"), "", 120),
        "correctAnswers": safe_string_list(generated.get("correctAnswers"), [], 6),
        "selectionMode": "multiple" if question_type == "multi-select" else "single",
        "blanks": _normalized_blanks(generated),
        "answerSpec": _normalized_answer_spec(generated),
        "interaction": normalize_question_interaction(generated.get("interaction"), question_type),
        "givens": givens,
        "options": options,
        "imageReferences": selected_images or (safe_string_list(generated.get("imageReferences"), [], 4) if generated.get("imageReferences") else []),
    }
    guide_cards = _normalized_guide_cards(generated, knowledge_point)
    payload = question_payload(question, _normalized_steps(generated), run)
    lesson_store[question_id] = {"payload": payload, "guideCards": guide_cards}
    log_event(
        "model.generation.completed",
        provider=run.get("provider"),
        model=run.get("model"),
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
        question_type=question_type,
    )
    return payload, guide_cards, run


def attach_question_source(
    payload: dict[str, Any],
    batch: dict[str, Any],
    ocr_run: dict[str, Any],
    source_image_references: list[str] | None = None,
) -> None:
    """Attach source lineage without leaking images from adjacent questions."""
    question = payload["question"]
    question["sourceBatchId"] = batch["id"]
    question["sourcePages"] = {
        "start": ocr_run.get("startPage", batch["startPage"]),
        "end": ocr_run.get("endPage", batch["endPage"]),
    }
    question["sourceArtifactUrl"] = ocr_run.get("sourceArtifactUrl")
    question["promptArtifactUrl"] = ocr_run.get("promptArtifactUrl")
    available_images = [
        url for url in ocr_run.get("imageUrls", [])
        if isinstance(url, str) and url.startswith("/api/uploads/")
    ]
    references = source_image_references or question.pop("imageReferences", [])
    available_by_name = {Path(url).name: url for url in available_images}
    question["imageUrls"] = [
        available_by_name[Path(reference).name]
        for reference in references
        if Path(reference).name in available_by_name
    ]


def review_lesson_payload(
    payload: dict[str, Any],
    lesson_source: str,
    asset_dir: Path | list[Path],
    guide_cards: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the review adapter and refresh the in-process lesson cache."""
    _number, question_block, _images = select_complete_question_source(lesson_source)
    image_paths = asset_dir if isinstance(asset_dir, list) else [
        asset_dir / Path(url).name
        for url in payload["question"].get("imageUrls", [])
        if (asset_dir / Path(url).name).is_file()
    ]
    reviewed_payload, review_run = runtime_reviewer.review(payload, question_block, image_paths)
    lesson_store[reviewed_payload["question"]["id"]] = {
        "payload": reviewed_payload,
        "guideCards": guide_cards,
    }
    return reviewed_payload, review_run


tutor_engine = TutorEngine(lesson_store=lesson_store, runtime=runtime, guide_cards=GUIDE_CARDS)


def generate_model_reply(request: HelpRequest) -> TutorReply:
    """Generate one tutoring turn from cached lesson context."""
    return tutor_engine.reply(request)
