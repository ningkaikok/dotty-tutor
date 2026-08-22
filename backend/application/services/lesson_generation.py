"""独立于 HTTP 和上传路由的模型课程生成能力。

函数只接收文本/字典并返回可序列化数据。与 ``app.py`` 分离后，模型行为无需构造分块上传请求即可测试，
也能被未来的后台 Worker 直接复用。
"""

from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path
from typing import Any

from application.services.tutor_engine import TutorEngine
from domain.questions.contracts import (
    CANVAS_ACTIONS,
    GUIDE_CARDS,
    LESSON_SCHEMA,
    LESSON_STEPS,
    QUESTION,
    HelpRequest,
    TutorReply,
)
from domain.questions.pipeline import (
    build_lesson_prompt,
    clean_question_stem,
    normalize_model_math_text,
    normalize_question_interaction,
    normalize_text_choices_from_source,
    strip_choice_text_from_prompt,
)
from domain.questions.source import (
    safe_string_list,
    safe_text,
    select_complete_question_source,
)
from domain.tutoring.checks import (
    generic_guide_cards,
    is_geometry_question,
    mock_model_run,
    normalize_guide_cards,
)
from infrastructure.runtime.model_runtime import runtime
from infrastructure.runtime.review_runtime import runtime_reviewer
from observability import log_event

# 该缓存仅加速单进程 Demo，PostgreSQL 才是持久化课程的真相来源。
# 多 Worker 部署应改用共享缓存或 Store，不能尝试在进程间同步这个字典。
lesson_store: dict[str, dict[str, Any]] = {}


def new_question_id(prefix: str, source: str) -> str:
    """为每次生成分配新的修订 ID。

    来源哈希仍用于来源证据和 ``sourceQuestionKey``，但不能充当题目主键；否则
    ``force=true`` 重新调用模型后会覆盖旧题并让前端误以为没有生成新版本。
    """
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{source_hash}-{uuid.uuid4().hex[:8]}"


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


def _normalized_steps(generated: dict[str, Any], question: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    raw_steps = generated.get("lessonSteps") if isinstance(generated.get("lessonSteps"), list) else []
    steps: list[dict[str, Any]] = []
    for index in range(4):
        raw = raw_steps[index] if index < len(raw_steps) and isinstance(raw_steps[index], dict) else {}
        steps.append({
            "id": f"model-step-{index + 1}",
            "title": safe_text(raw.get("title"), f"第 {index + 1} 步", 80),
            # 题干和选项会经过统一的公式规范化；讲解步骤也必须走同一条路径，
            # 否则生产端会出现“题目能渲染、讲解仍显示 $...$”的分裂行为。
            "text": normalize_model_math_text(safe_text(raw.get("text"), "根据题目条件继续推理。", 700)),
            "speechText": normalize_model_math_text(safe_text(raw.get("speechText"), "我们继续看下一步。", 700)),
            # 目前只有几何题有具体画布动作；其他题保留统一的基础画布，
            # 避免历史几何样例污染普通数学题的讲解状态。
            "action": CANVAS_ACTIONS[index] if is_geometry_question(question) else "show-base",
        })
    return steps


def _normalized_guide_cards(
    generated: dict[str, Any],
    knowledge_point: str,
    question: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
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
            "canvasAction": CANVAS_ACTIONS[min(index + 1, 3)] if is_geometry_question(question) else "show-base",
        })
    return normalize_guide_cards(cards, question)


def _fallback_lesson(
    source: str,
    selected_number: str,
    selected_source: str,
    selected_images: list[str],
    model_run: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """在模型不可用时保留 OCR 原题，而不是返回内置几何演示题。"""
    prompt = clean_question_stem(selected_number, selected_source) if selected_number else source[:4_000]
    question = {
        "id": new_question_id("fallback", source),
        "questionType": "short-answer",
        "chapter": "教材练习",
        "knowledgePoint": "待确认知识点",
        "questionNumber": selected_number,
        "prompt": normalize_model_math_text(prompt or "暂时无法生成题目，请重新尝试。"),
        "correctAnswer": "",
        "correctAnswers": [],
        "selectionMode": "single",
        "blanks": [],
        "answerSpec": None,
        "interaction": {"type": "none", "instruction": "", "points": [], "requiredConnections": []},
        "givens": [],
        # 先放一个占位，让已有 OCR 选项拆分器可以识别连续的 A-D 选项。
        "options": [""],
        "imageReferences": selected_images,
    }
    payload = question_payload(
        question,
        _normalized_steps({}, question),
        model_run,
    )
    # 复用已有的 OCR 选项拆分规则；即使没有完整结构化结果，也不要把 A-D 丢掉。
    normalize_text_choices_from_source(payload, selected_source or source)
    payload["question"]["prompt"] = normalize_model_math_text(strip_choice_text_from_prompt(
        payload["question"]["prompt"],
        payload["question"].get("options", []),
    ))
    if len(payload["question"].get("options", [])) >= 2:
        payload["question"]["questionType"] = "choice"
    cards = generic_guide_cards(payload["question"])
    return payload, cards


def generate_lesson(
    source_text: str,
    *,
    repair_errors: list[str] | None = None,
) -> tuple[dict, list[dict[str, Any]], dict[str, Any]]:
    """Generate one validated-shape lesson, optionally repairing known errors."""
    selection = runtime.selection
    started = time.perf_counter()
    log_event("model.generation.started", provider=selection.provider, model=selection.model)
    provided_source = source_text.strip()[:16_000]
    source = provided_source
    if not source:
        source = f"{QUESTION['prompt']}\n已知条件：{'；'.join(QUESTION['givens'])}"
    selected_number, selected_source, selected_images = select_complete_question_source(source)
    if selected_number:
        source = selected_source

    if selection.provider == "mock":
        run = mock_model_run()
        if not provided_source:
            payload = question_payload(model_run=run)
            cards = GUIDE_CARDS
        else:
            payload, cards = _fallback_lesson(source, selected_number, selected_source, selected_images, run)
        lesson_store[payload["question"]["id"]] = {"payload": payload, "guideCards": cards}
        log_event("model.generation.completed", provider="mock", duration_ms=round((time.perf_counter() - started) * 1000, 1))
        return payload, cards, run

    try:
        generated, run = runtime.generate_json(
            build_lesson_prompt(source, repair_errors),
            LESSON_SCHEMA,
            max_tokens=1600,
        )
    except Exception as error:
        run = mock_model_run(selection.provider, str(error))
        payload, cards = _fallback_lesson(source, selected_number, selected_source, selected_images, run)
        lesson_store[payload["question"]["id"]] = {"payload": payload, "guideCards": cards}
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
        return payload, cards, run

    question_id = new_question_id("generated", source)
    question_type = safe_text(generated.get("questionType"), "short-answer", 30)
    if question_type not in {
        "choice", "multi-select", "true-false", "short-answer", "fill-blank", "numeric", "draw-line",
    }:
        question_type = "short-answer"
    givens = [
        item for item in safe_string_list(generated.get("givens"), [], 5)
        if "images/" not in item and "![" not in item
    ]
    options = [
        normalize_model_math_text(option)
        for option in (safe_string_list(generated.get("options"), [], 6) if generated.get("options") else [])
    ]
    prompt = clean_question_stem(selected_number, selected_source) if selected_number else safe_text(generated.get("prompt"), QUESTION["prompt"], 4000)
    knowledge_point = safe_text(generated.get("knowledgePoint"), "分步推理", 120)
    question = {
        "id": question_id,
        "questionType": question_type,
        "chapter": safe_text(generated.get("chapter"), "教材练习", 80),
        "knowledgePoint": knowledge_point,
        "questionNumber": selected_number or safe_text(generated.get("questionNumber"), "", 30),
        "prompt": normalize_model_math_text(strip_choice_text_from_prompt(prompt, options)),
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
    guide_cards = _normalized_guide_cards(generated, knowledge_point, question)
    payload = question_payload(question, _normalized_steps(generated, question), run)
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
    source_image_references: list[str],
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
    # ``[]`` 是一个有意义的结果：OCR 已明确判断本题没有图片。
    # 不能用 ``or`` 回退到模型返回的 imageReferences，否则模型可能把同一批次
    # 其他题目的图片重新带进来，造成题干图/选项图串题。
    references = list(source_image_references)
    question.pop("imageReferences", None)
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
