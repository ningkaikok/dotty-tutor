from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import tempfile
import time
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from pypdf import PdfReader

from model_runtime import runtime
from ocr_runtime import runtime as ocr_runtime
from review_runtime import runtime_reviewer
from storage import store
from answer_evaluator import evaluate_structured_answer
from runtime_routes import build_runtime_router
from question_pipeline import (
    apply_question_quality_gate,
    build_lesson_prompt,
    build_question_content_blocks,
    clean_question_stem,
    normalize_image_choice_question,
    normalize_model_math_text,
    normalize_question_interaction,
    normalize_stacked_equation_choices,
    normalize_text_choice_labels,
    normalize_text_choices_from_source,
    rich_text_blocks,
    split_concatenated_text_choices,
    strip_choice_text_from_prompt,
    validate_question_payload,
    write_model_prompt_artifact,
)
from question_contracts import (
    CANVAS_ACTIONS,
    GUIDE_CARDS,
    HELP_SCHEMA,
    LESSON_SCHEMA,
    LESSON_STEPS,
    QUESTION,
    HelpRequest,
    PdfUploadInitRequest,
    TutorReply,
)


app = FastAPI(title="Dotty Tutor", version="0.1.0")


def csv_env(name: str, default: str) -> list[str]:
    """Read a comma-separated environment variable without empty entries."""
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


cors_origins = csv_env(
    "CORS_ORIGINS",
    "http://localhost:5174,http://127.0.0.1:5174",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
)

trusted_hosts = csv_env("TRUSTED_HOSTS", "")
if trusted_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Add low-risk browser hardening and a request correlation header."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "microphone=(self)")
    return response


ALLOWED_UPLOAD_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif",
    ".gif", ".bmp", ".tif", ".tiff", ".pdf",
}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
PDF_MAX_UPLOAD_BYTES = 500 * 1024 * 1024
PDF_CHUNK_BYTES = 5 * 1024 * 1024
PDF_BATCH_PAGES = 5
PDF_TAIL_CHECK_BYTES = 64 * 1024
UPLOAD_ROOT = store.upload_root
pdf_uploads: dict[str, dict[str, Any]] = {}
lesson_store: dict[str, dict[str, Any]] = {}
def mock_model_run(requested_provider: str = "mock", error: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "requestedProvider": requested_provider,
        "provider": "mock",
        "model": "static-demo",
        "fallback": requested_provider != "mock",
    }
    if error:
        result["error"] = error
    return result


def build_reply(
    request: HelpRequest,
    guide_cards: list[dict[str, Any]] | None = None,
    model_run: dict[str, Any] | None = None,
) -> TutorReply:
    normalized = request.studentInput.strip().lower()
    correct_markers = ("垂直平分线", "perpendicular bisector")
    cards = guide_cards or GUIDE_CARDS
    run = model_run or mock_model_run()

    if any(marker in normalized for marker in correct_markers):
        guide_context = {
            "assessment": "correct",
            "stuckAt": "学生已经提出正确猜想，需要补全证明。",
            "knowledge": ["全等三角形", "垂直平分线"],
            "hint": "不要停在结论；用 PA = PB、AM = BM 和公共边 PM 说明理由。",
            "question": "你能用 SSS 全等把这个结论证明完整吗？",
        }
        return TutorReply(
            reply="这个猜想是对的。先别急着结束：请说明三角形 PAM 与 PBM 为什么全等。",
            guideContext=guide_context,
            nextHintLevel=min(request.hintLevel + 1, 3),
            canvasAction="show-triangles",
            source="answer-check",
            modelRun=run,
        )

    card = cards[min(request.hintLevel, len(cards) - 1)]
    stuck_markers = ("不知道", "不会", "没思路", "卡住", "don't know", "stuck")
    has_attempt = bool(normalized) and not any(marker in normalized for marker in stuck_markers)
    if request.mode == "answer" and has_attempt:
        prefix = "我会先核对你写的这一步。"
    else:
        prefix = "我看到你已经写了一些思路。" if has_attempt else "没关系，我们只往前走一步。"
    reply = f"{prefix}{card['hint']}\n\n{card['question']}"
    return TutorReply(
        reply=reply,
        guideContext={
            "assessment": "partial" if has_attempt else "incorrect",
            "stuckAt": card["stuckAt"],
            "knowledge": card["knowledge"],
            "hint": card["hint"],
            "question": card["question"],
        },
        nextHintLevel=min(request.hintLevel + 1, 3),
        canvasAction=card["canvasAction"],
        source="stored-guide-card",
        modelRun=run,
    )


def question_payload(
    question: dict[str, Any] | None = None,
    lesson_steps: list[dict[str, Any]] | None = None,
    model_run: dict[str, Any] | None = None,
) -> dict:
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


app.include_router(build_runtime_router(store=store, question_payload=question_payload))


def safe_text(value: Any, fallback: str, limit: int = 600) -> str:
    text = str(value or "").strip()
    return (text or fallback)[:limit]


def safe_string_list(value: Any, fallback: list[str], limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return fallback
    items = [safe_text(item, "", 160) for item in value]
    items = [item for item in items if item]
    return items[:limit] or fallback


def extract_pdf_text(reader: PdfReader, max_pages: int = 10, max_chars: int = 16_000) -> str:
    pages: list[str] = []
    for page in reader.pages[:max_pages]:
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""
        if text:
            pages.append(text)
        if sum(len(item) for item in pages) >= max_chars:
            break
    return "\n\n".join(pages)[:max_chars]


def resolve_ocr_text(
    source_text: str,
    extracted_text: str = "",
    source_path: Path | None = None,
    start_page: int = 0,
    end_page: int | None = None,
    asset_dir: Path | None = None,
    asset_url_prefix: str = "",
) -> tuple[str, dict[str, Any]]:
    if source_text.strip():
        return source_text.strip(), {
            "requestedProvider": "manual",
            "provider": "manual",
            "mode": "pasted-text",
            "fallback": False,
            "output": "text",
        }

    requested = ocr_runtime.selection.provider
    if source_path and ocr_runtime.should_use_mineru():
        try:
            return ocr_runtime.parse(
                source_path,
                start_page,
                end_page,
                asset_dir,
                asset_url_prefix,
            )
        except Exception as error:
            return extracted_text, {
                "requestedProvider": requested,
                "provider": "pypdf" if extracted_text else "none",
                "mode": "text-layer-fallback" if extracted_text else "ocr-failed",
                "fallback": True,
                "error": str(error),
                "output": "text",
            }

    return extracted_text, {
        "requestedProvider": requested,
        "provider": "pypdf" if extracted_text else "none",
        "mode": "text-layer" if extracted_text else "no-text-layer",
        "fallback": False,
        "output": "text",
    }


EQUATION_PATTERN = re.compile(
    r"(?<![0-9A-Za-z])([0-9A-Za-z]+(?:\s*[+\-*/]\s*[0-9A-Za-z]+)*\s*=\s*"
    r"-?\s*[0-9A-Za-z]+(?:\s*[+\-*/]\s*[0-9A-Za-z]+)*)(?![0-9A-Za-z])"
)


def linear_equation_form(equation: str) -> tuple[float, float] | None:
    if equation.count("=") != 1:
        return None

    def expression_form(expression: str) -> tuple[float, float] | None:
        normalized = expression.replace(" ", "").replace("*", "")
        terms = re.findall(r"[+-]?[^+-]+", normalized)
        coefficient = 0.0
        constant = 0.0
        try:
            for term in terms:
                if term.endswith("x"):
                    shown = term[:-1]
                    coefficient += 1.0 if shown in ("", "+") else -1.0 if shown == "-" else float(shown)
                elif "x" in term or not term:
                    return None
                else:
                    constant += float(term)
        except ValueError:
            return None
        return coefficient, constant

    left, right = equation.split("=", 1)
    left_form = expression_form(left)
    right_form = expression_form(right)
    if not left_form or not right_form:
        return None
    return left_form[0] - right_form[0], left_form[1] - right_form[1]


def equivalent_linear_equations(first: str, second: str) -> bool | None:
    first_form = linear_equation_form(first)
    second_form = linear_equation_form(second)
    if not first_form or not second_form:
        return None
    first_a, first_b = first_form
    second_a, second_b = second_form
    if abs(first_a) < 1e-9 or abs(second_a) < 1e-9:
        return None
    return abs(first_a * second_b - second_a * first_b) < 1e-7


def equation_conflict(
    student_input: str,
    lesson_steps: list[dict[str, Any]],
    question_prompt: str = "",
) -> tuple[str, str] | None:
    student_equations = [re.sub(r"\s+", "", item) for item in EQUATION_PATTERN.findall(student_input)]
    question_equations = [re.sub(r"\s+", "", item) for item in EQUATION_PATTERN.findall(question_prompt)]
    for student_equation in student_equations:
        for question_equation in question_equations:
            equivalent = equivalent_linear_equations(student_equation, question_equation)
            if equivalent is False:
                return student_equation, question_equation

    reference_text = "\n".join(
        f"{step.get('text', '')} {step.get('speechText', '')}" for step in lesson_steps
    )
    reference_equations = [re.sub(r"\s+", "", item) for item in EQUATION_PATTERN.findall(reference_text)]
    for student_equation in student_equations:
        student_left = student_equation.split("=", 1)[0]
        for reference_equation in reference_equations:
            if reference_equation.split("=", 1)[0] == student_left and reference_equation != student_equation:
                return student_equation, reference_equation
    return None


QUESTION_START_PATTERN = re.compile(r"(?m)^\s*(?P<number>\d{1,3})[.．、]\s*")
MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
MAX_QUESTIONS_PER_BATCH = 5


def split_question_sources(source: str) -> list[tuple[str, str, list[str]]]:
    """Split OCR Markdown into complete numbered questions before answers."""
    question_area = re.split(r"(?m)^\s*#*\s*(?:参考答案|答案|解析)\s*$", source, maxsplit=1)[0]
    matches = list(QUESTION_START_PATTERN.finditer(question_area))
    blocks: list[tuple[str, str, list[str]]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(question_area)
        block = question_area[match.start():end].strip()
        if len(block) < 4:
            continue
        images = MARKDOWN_IMAGE_PATTERN.findall(block)
        blocks.append((match.group("number"), block, images))
    return blocks


def limited_question_sources(source: str) -> list[tuple[str, str, list[str]]]:
    blocks = split_question_sources(source)[:MAX_QUESTIONS_PER_BATCH]
    return blocks or [("", source, MARKDOWN_IMAGE_PATTERN.findall(source))]


def select_complete_question_source(source: str) -> tuple[str, str, list[str]]:
    """Select one intact illustrated question, kept for compatibility."""
    blocks = split_question_sources(source)
    if not blocks:
        return "", source.strip(), MARKDOWN_IMAGE_PATTERN.findall(source)

    # A diagram-dependent problem proves the end-to-end image path and is
    # usually a better interactive tutoring example. Otherwise use the first
    # complete numbered problem in the batch.
    return next((block for block in blocks if block[2]), blocks[0])


def question_image_paths(asset_dir: Path, references: list[str]) -> list[Path]:
    """Resolve only the images referenced by one question, in source order."""
    available = {
        path.name: path
        for path in asset_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    } if asset_dir.is_dir() else {}
    return [available[Path(reference).name] for reference in references if Path(reference).name in available]


def question_key(batch_id: str, number: str, index: int) -> str:
    normalized = re.sub(r"[^0-9A-Za-z_-]+", "", number) or f"index-{index + 1:03d}"
    return f"{batch_id}-q-{normalized}"


def process_question_sources(
    question_sources: list[tuple[str, str, list[str]]],
    batch: dict[str, Any],
    ocr_run: dict[str, Any],
    asset_dir: Path,
    job: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate, attach, review and return every question in one OCR batch."""
    payloads: list[dict[str, Any]] = []
    guide_cards_list: list[list[dict[str, Any]]] = []
    model_runs: list[dict[str, Any]] = []
    review_runs: list[dict[str, Any]] = []
    total = max(1, len(question_sources))
    for index, (number, block, images) in enumerate(question_sources):
        payload, guide_cards, model_run = generate_lesson(block)
        attach_question_source(payload, batch, ocr_run, images)
        if number:
            payload["question"]["questionNumber"] = number
        payload["question"]["sourceQuestionKey"] = question_key(batch["id"], number, index)
        payload, review_run = review_lesson_payload(
            payload,
            block,
            question_image_paths(asset_dir, images),
            guide_cards,
        )
        normalize_stacked_equation_choices(payload, block)
        normalize_text_choices_from_source(
            payload, str(payload["question"].get("prompt", ""))
        )
        normalize_text_choices_from_source(payload, block)
        reviewed_options = normalize_text_choice_labels(
            safe_string_list(payload["question"].get("options"), [], 6)
        )
        reviewed_options = [normalize_model_math_text(option) for option in reviewed_options]
        payload["question"]["options"] = reviewed_options
        payload["question"]["prompt"] = normalize_model_math_text(
            strip_choice_text_from_prompt(
                str(payload["question"].get("prompt", "")), reviewed_options
            )
        )
        normalize_image_choice_question(payload, block, images)
        apply_question_quality_gate(payload, block, images)
        payloads.append(payload)
        guide_cards_list.append(guide_cards)
        model_runs.append(model_run)
        review_runs.append(review_run)
        if job is not None:
            update_upload_job(
                job,
                "generating",
                min(94, 88 + round(((index + 1) / total) * 6)),
                f"正在处理第 {index + 1}/{len(question_sources)} 道题",
            )
    return payloads, guide_cards_list, model_runs, review_runs


def generate_lesson(source_text: str) -> tuple[dict, list[dict[str, Any]], dict[str, Any]]:
    selection = runtime.selection
    source = source_text.strip()[:16_000]
    if not source:
        source = f"{QUESTION['prompt']}\n已知条件：{'；'.join(QUESTION['givens'])}"
    selected_number, selected_source, selected_images = select_complete_question_source(source)
    if selected_number:
        source = selected_source

    prompt = build_lesson_prompt(source)

    if selection.provider == "mock":
        run = mock_model_run()
        payload = question_payload(model_run=run)
        lesson_store[QUESTION["id"]] = {"payload": payload, "guideCards": GUIDE_CARDS}
        return payload, GUIDE_CARDS, run

    try:
        generated, run = runtime.generate_json(prompt, LESSON_SCHEMA, max_tokens=1600)
    except Exception as error:
        run = mock_model_run(selection.provider, str(error))
        payload = question_payload(model_run=run)
        lesson_store[QUESTION["id"]] = {"payload": payload, "guideCards": GUIDE_CARDS}
        return payload, GUIDE_CARDS, run

    question_id = f"generated-{hashlib.sha256(source.encode('utf-8')).hexdigest()[:12]}"
    question_type = safe_text(generated.get("questionType"), "short-answer", 30)
    if question_type not in {
        "choice", "multi-select", "true-false", "short-answer", "fill-blank", "numeric", "draw-line",
    }:
        question_type = "short-answer"
    generated_givens = safe_string_list(generated.get("givens"), [], 5)
    generated_givens = [
        given for given in generated_givens
        if "images/" not in given and "![" not in given
    ]
    generated_options = safe_string_list(generated.get("options"), [], 6) if generated.get("options") else []
    generated_correct_answers = safe_string_list(generated.get("correctAnswers"), [], 6)
    generated_blanks: list[dict[str, Any]] = []
    if isinstance(generated.get("blanks"), list):
        for index, raw_blank in enumerate(generated["blanks"][:8], start=1):
            if not isinstance(raw_blank, dict):
                continue
            blank_id = safe_text(raw_blank.get("id"), f"blank-{index}", 24)
            try:
                tolerance = max(0.0, float(raw_blank.get("tolerance", 0) or 0))
            except (TypeError, ValueError):
                tolerance = 0.0
            generated_blanks.append({
                "id": blank_id,
                "label": safe_text(raw_blank.get("label"), f"第 {index} 空", 30),
                "answerType": safe_text(raw_blank.get("answerType"), "text", 20),
                "correctAnswers": safe_string_list(raw_blank.get("correctAnswers"), [], 6),
                "tolerance": tolerance,
                "unit": safe_text(raw_blank.get("unit"), "", 20),
            })
    generated_answer_spec = generated.get("answerSpec") if isinstance(generated.get("answerSpec"), dict) else None
    answer_spec = None
    if generated_answer_spec:
        try:
            tolerance = max(0.0, float(generated_answer_spec.get("tolerance", 0) or 0))
        except (TypeError, ValueError):
            tolerance = 0.0
        answer_spec = {
            "answerType": safe_text(generated_answer_spec.get("answerType"), "numeric", 20),
            "expected": safe_text(generated_answer_spec.get("expected"), "", 120),
            "accepted": safe_string_list(generated_answer_spec.get("accepted"), [], 6),
            "tolerance": tolerance,
            "unit": safe_text(generated_answer_spec.get("unit"), "", 20),
        }
    question_prompt = clean_question_stem(selected_number, selected_source) if selected_number else safe_text(generated.get("prompt"), QUESTION["prompt"], 4000)
    question_prompt = strip_choice_text_from_prompt(question_prompt, generated_options)
    question = {
        "id": question_id,
        "questionType": question_type,
        "chapter": safe_text(generated.get("chapter"), "教材练习", 80),
        "knowledgePoint": safe_text(generated.get("knowledgePoint"), "分步推理", 120),
        "questionNumber": selected_number or safe_text(generated.get("questionNumber"), "", 30),
        "prompt": question_prompt,
        "correctAnswer": safe_text(generated.get("correctAnswer"), "", 120),
        "correctAnswers": generated_correct_answers,
        "selectionMode": "multiple" if question_type == "multi-select" else "single",
        "blanks": generated_blanks,
        "answerSpec": answer_spec,
        "interaction": normalize_question_interaction(generated.get("interaction"), question_type),
        "givens": generated_givens,
        "options": generated_options,
        "imageReferences": selected_images or (safe_string_list(generated.get("imageReferences"), [], 4) if generated.get("imageReferences") else []),
    }
    raw_steps = generated.get("lessonSteps") if isinstance(generated.get("lessonSteps"), list) else []
    lesson_steps = []
    for index in range(4):
        raw = raw_steps[index] if index < len(raw_steps) and isinstance(raw_steps[index], dict) else {}
        lesson_steps.append(
            {
                "id": f"model-step-{index + 1}",
                "title": safe_text(raw.get("title"), f"第 {index + 1} 步", 80),
                "text": safe_text(raw.get("text"), "根据题目条件继续推理。", 700),
                "speechText": safe_text(raw.get("speechText"), "我们继续看下一步。", 700),
                "action": CANVAS_ACTIONS[index],
            }
        )

    raw_cards = generated.get("guideCards") if isinstance(generated.get("guideCards"), list) else []
    guide_cards = []
    for index in range(3):
        fallback = {
            "stuckAt": "需要把题目条件转化为下一步操作。",
            "knowledge": [question["knowledgePoint"]],
            "hint": "先圈出题目明确给出的量，再判断当前能进行哪一步。",
            "question": "根据已知条件，你现在可以先写出什么关系？",
        }
        raw = raw_cards[index] if index < len(raw_cards) and isinstance(raw_cards[index], dict) else {}
        guide_cards.append(
            {
                "level": index,
                "stuckAt": safe_text(raw.get("stuckAt"), fallback["stuckAt"], 300),
                "knowledge": safe_string_list(raw.get("knowledge"), fallback["knowledge"]),
                "hint": safe_text(raw.get("hint"), fallback["hint"], 500),
                "question": safe_text(raw.get("question"), fallback["question"], 500),
                "canvasAction": CANVAS_ACTIONS[min(index + 1, 3)],
            }
        )

    payload = question_payload(question, lesson_steps, run)
    lesson_store[question_id] = {"payload": payload, "guideCards": guide_cards}
    return payload, guide_cards, run


def attach_question_source(
    payload: dict[str, Any],
    batch: dict[str, Any],
    ocr_run: dict[str, Any],
    source_image_references: list[str] | None = None,
) -> None:
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
    # Preserve the order in which MinerU references images in the question.
    # Sorting asset filenames changes A/B/C/D into an unrelated visual order.
    matched_images = [
        available_by_name[Path(reference).name]
        for reference in references
        if Path(reference).name in available_by_name
    ]
    # A question without an image reference must remain image-free. Falling
    # back to the first batch assets leaks images from earlier questions.
    question["imageUrls"] = matched_images


def review_lesson_payload(
    payload: dict[str, Any],
    lesson_source: str,
    asset_dir: Path | list[Path],
    guide_cards: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    _number, question_block, _images = select_complete_question_source(lesson_source)
    if isinstance(asset_dir, list):
        image_paths = asset_dir
    else:
        image_paths = [
            asset_dir / Path(url).name
            for url in payload["question"].get("imageUrls", [])
            if (asset_dir / Path(url).name).is_file()
        ]
    reviewed_payload, review_run = runtime_reviewer.review(
        payload,
        question_block,
        image_paths,
    )
    lesson_store[reviewed_payload["question"]["id"]] = {
        "payload": reviewed_payload,
        "guideCards": guide_cards,
    }
    return reviewed_payload, review_run


def generate_model_reply(request: HelpRequest) -> TutorReply:
    stored = lesson_store.get(request.questionId)
    if stored and request.mode == "answer":
        question = stored["payload"].get("question", {})
        structured = evaluate_structured_answer(
            question,
            request.studentInput,
            request.interactionResult,
        )
        if structured:
            return TutorReply(
                reply=structured["reply"],
                guideContext={
                    key: structured[key]
                    for key in ("assessment", "stuckAt", "knowledge", "hint", "question")
                },
                nextHintLevel=min(request.hintLevel + 1, 3),
                canvasAction="show-base",
                source="answer-check",
                modelRun=mock_model_run(),
            )
        if question.get("questionType") == "true-false" and question.get("correctAnswer"):
            expected = str(question["correctAnswer"]).strip().lower()
            submitted = request.studentInput.strip().lower()
            selected = "正确" if "正确" in submitted or "true" in submitted else "错误" if "错误" in submitted or "false" in submitted else ""
            if selected:
                is_correct = selected.lower() == expected
                assessment = "correct" if is_correct else "incorrect"
                reply = (
                    f"回答正确，答案是“{question['correctAnswer']}”。请再说说题干中的哪个条件支持这个判断。"
                    if is_correct else
                    f"这次选择不对，正确答案是“{question['correctAnswer']}”。请回到题干，找出能验证这句话的条件。"
                )
                return TutorReply(
                    reply=reply,
                    guideContext={
                        "assessment": assessment,
                        "stuckAt": "需要根据题干条件判断命题真伪。",
                        "knowledge": [question.get("knowledgePoint", "概念判断")],
                        "hint": "圈出题干中的关键条件，再逐项核对命题。",
                        "question": "题干中的哪条条件能支持你的判断？",
                    },
                    nextHintLevel=min(request.hintLevel + 1, 3),
                    canvasAction="show-base",
                    source="answer-check",
                    modelRun=mock_model_run(),
                )
    if stored and request.mode == "answer":
        question = stored["payload"].get("question", {})
        if question.get("questionType") == "draw-line":
            interaction = question.get("interaction") or {}
            required = {
                tuple(sorted(pair))
                for pair in interaction.get("requiredConnections", [])
                if isinstance(pair, list) and len(pair) == 2
            }
            submitted = {
                tuple(sorted(pair))
                for pair in request.interactionResult.get("connections", [])
                if isinstance(pair, list) and len(pair) == 2
            }
            if required:
                is_correct = required.issubset(submitted)
                assessment = "correct" if is_correct else "partial" if submitted else "incorrect"
                reply = (
                    "连接正确。请说明这条线段为什么满足题目要求。"
                    if is_correct else
                    "还差一点：检查是否连接了题目要求的两个端点，再试一次。"
                )
                return TutorReply(
                    reply=reply,
                    guideContext={
                        "assessment": assessment,
                        "stuckAt": "需要把题目中的几何关系落实为图上的连线。",
                        "knowledge": [question.get("knowledgePoint", "几何作图")],
                        "hint": interaction.get("instruction", "先找出题目要求连接的两个点。"),
                        "question": "你连接的线段对应题目中的哪条几何关系？",
                    },
                    nextHintLevel=min(request.hintLevel + 1, 3),
                    canvasAction="show-triangles",
                    source="answer-check",
                    modelRun=mock_model_run(),
                )
    if not stored or runtime.selection.provider == "mock":
        cards = stored["guideCards"] if stored else GUIDE_CARDS
        return build_reply(request, cards)

    payload = stored["payload"]
    cards = stored["guideCards"]
    current_card = cards[min(request.hintLevel, len(cards) - 1)]
    conflict = equation_conflict(
        request.studentInput,
        payload["lessonSteps"],
        payload["question"]["prompt"],
    )
    conflict_instruction = ""
    if conflict:
        conflict_instruction = (
            f"系统校验发现学生写的 {conflict[0]} 与标准步骤 {conflict[1]} 冲突。"
            "assessment 必须为 incorrect，绝对不能说这一步正确；只提示学生回查符号和算术。"
        )
    prompt = f"""
你正在辅导下面这道题。先用标准讲解脚本独立核对学生的每一步计算，再判断卡点。

题目：{payload['question']['prompt']}
已知条件：{'；'.join(payload['question']['givens'])}
标准讲解脚本：{json_dumps(payload['lessonSteps'])}
当前提示层级：{request.hintLevel}
候选引导卡：{json_dumps(current_card)}
学生输入：{request.studentInput.strip() or '学生没有输入内容'}
学生交互作答结果：{json_dumps(request.interactionResult) if request.interactionResult else '无'}
用户操作：{'提交回答并请求判题' if request.mode == 'answer' else '请求下一步提示'}
系统确定性校验：{conflict_instruction or '未发现同左边等式冲突，仍需自行核对。'}

要求：
1. assessment 必须是 correct、partial 或 incorrect。
2. 特别核对移项符号、算术和单位；只有确实正确时才能说“对”或表扬该步骤。
3. 如果错误，温和但明确指出哪一步不成立，然后给一个不泄露最终答案的提示。
4. 如果用户是请求提示，只引导下一步，不给最终答案；如果是提交回答，先明确判断再引导修改或继续。
5. reply 应像真人老师一样简短，最后提一个学生可以继续回答的问题。
""".strip()
    selection = runtime.selection
    try:
        generated, run = runtime.generate_json(prompt, HELP_SCHEMA, max_tokens=450)
    except Exception as error:
        return build_reply(
            request,
            cards,
            mock_model_run(selection.provider, str(error)),
        )

    action = generated.get("canvasAction")
    if action not in CANVAS_ACTIONS:
        action = current_card["canvasAction"]
    assessment = generated.get("assessment", "partial")
    reply_text = safe_text(generated.get("reply"), current_card["hint"], 1000)
    if conflict:
        assessment = "incorrect"
        reply_text = (
            f"这里需要再核对一下：你写的 {conflict[0]} 与前一步推导不一致。"
            "先别继续除法，请重新检查移项后的符号和右边的计算，你能重算这一行吗？"
        )
        action = current_card["canvasAction"]
    return TutorReply(
        reply=reply_text,
        guideContext={
            "assessment": assessment,
            "stuckAt": safe_text(generated.get("stuckAt"), current_card["stuckAt"], 300),
            "knowledge": safe_string_list(generated.get("knowledge"), current_card["knowledge"]),
            "hint": safe_text(generated.get("hint"), current_card["hint"], 500),
            "question": safe_text(generated.get("question"), current_card["question"], 500),
        },
        nextHintLevel=min(request.hintLevel + 1, 3),
        canvasAction=action,
        source="model-generated",
        modelRun=run,
    )


def json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def upload_job(upload_id: str) -> dict[str, Any]:
    job = pdf_uploads.get(upload_id)
    if not job:
        job = store.load_job(upload_id)
        if job:
            pdf_uploads[upload_id] = job
            for batch_id, payload in job.get("batchPayloads", {}).items():
                lesson_store[payload["question"]["id"]] = {
                    "payload": payload,
                    "guideCards": job.get("batchGuideCards", {}).get(batch_id) or GUIDE_CARDS,
                }
    if not job:
        raise HTTPException(status_code=404, detail="上传任务不存在")
    return job


def upload_status(job: dict[str, Any]) -> dict:
    uploaded = sorted(
        int(path.stem.split("-")[-1])
        for path in job["directory"].glob("chunk-*.part")
    )
    result = {
        "uploadId": job["uploadId"],
        "filename": job["filename"],
        "size": job["size"],
        "chunkSize": job["chunkSize"],
        "totalChunks": job["totalChunks"],
        "uploadedChunks": uploaded,
        "status": job["status"],
        "progress": job.get("progress", 0),
        "message": job.get("message", ""),
        "elapsedSeconds": round(
            ((job.get("completedAt") or time.time()) - job["startedAt"]),
            1,
        ),
    }
    if job.get("result"):
        result["result"] = job["result"]
    return result


def update_upload_job(
    job: dict[str, Any],
    status: str,
    progress: int,
    message: str,
) -> None:
    job["status"] = status
    job["progress"] = max(0, min(progress, 100))
    job["message"] = message
    job["updatedAt"] = time.time()
    store.save_job(job)


def validate_pdf_envelope(path: Path) -> None:
    """Reject truncated/non-PDF files before handing them to a PDF parser."""
    with path.open("rb") as pdf_file:
        header = pdf_file.read(8)
        pdf_file.seek(max(0, path.stat().st_size - PDF_TAIL_CHECK_BYTES))
        tail = pdf_file.read()

    if not header.startswith(b"%PDF-"):
        raise ValueError("文件头不是有效的 PDF（缺少 %PDF- 标记）")
    if b"%%EOF" not in tail:
        size_mb = path.stat().st_size / 1024 / 1024
        raise ValueError(
            "文件缺少 PDF 结束标记（%%EOF）。"
            f"分块已完整合并为 {size_mb:.1f} MB，因此原 PDF 很可能下载不完整或导出中断；"
            "请重新下载，或用系统的“打印 → 存储为 PDF”生成新文件后重试"
        )


@app.post("/api/textbook/import")
async def import_textbook(
    file: UploadFile = File(...),
    sourceText: str = Form(default="", max_length=20_000),
) -> dict:
    """读取一页教材并返回结构化结果；轻量导入路径不持久化原文件。"""
    filename = Path(file.filename or "textbook-page").name
    suffix = Path(filename).suffix.lower()
    content_type = (file.content_type or "").lower()
    is_supported_mime = content_type.startswith("image/") or content_type == "application/pdf"
    is_supported_suffix = suffix in ALLOWED_UPLOAD_SUFFIXES
    if not is_supported_mime and not is_supported_suffix:
        shown_type = content_type or "未提供 MIME"
        raise HTTPException(
            status_code=415,
            detail=f"不支持这个文件：{filename}（{shown_type}）。请上传图片或 PDF。",
        )

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件不能为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="教材页不能超过 10 MB")

    extracted_text = ""
    if suffix == ".pdf" or content_type == "application/pdf":
        try:
            reader = PdfReader(BytesIO(content))
            extracted_text = extract_pdf_text(reader)
        except Exception as error:
            raise HTTPException(status_code=422, detail=f"PDF 无法解析：{error}") from error

    if not sourceText.strip() and ocr_runtime.should_use_mineru():
        with tempfile.TemporaryDirectory(prefix="dotty-ocr-input-") as input_dir:
            source_path = Path(input_dir) / filename
            source_path.write_bytes(content)
            lesson_source, ocr_run = resolve_ocr_text(sourceText, extracted_text, source_path)
    else:
        lesson_source, ocr_run = resolve_ocr_text(sourceText, extracted_text)
    payload, guide_cards, model_run = generate_lesson(lesson_source)
    digest = hashlib.sha256(content).hexdigest()
    return {
        "importId": f"page-{digest[:12]}",
        "filename": filename,
        "contentType": content_type or "application/octet-stream",
        "size": len(content),
        "stored": False,
        "modelRun": model_run,
        "ocrRun": ocr_run,
        "stages": [
            {"id": "upload", "label": "文件校验", "status": "done"},
            {"id": "ocr", "label": "MinerU OCR / 文字层解析", "status": "done"},
            {"id": "structure", "label": "题目结构化", "status": "done"},
            {"id": "guides", "label": "引导卡生成", "status": "done"},
        ],
        "extraction": {
            "chapter": payload["question"]["chapter"],
            "knowledgePoint": payload["question"]["knowledgePoint"],
            "questionCount": 1,
            "formulaCount": 2,
            "guideCardCount": len(guide_cards),
            "confidence": 0.96,
            "mode": f"model-from-{ocr_run['provider']}" if lesson_source else "demo-seed-no-ocr",
        },
        "questionPayload": payload,
    }


@app.post("/api/uploads/init")
def init_pdf_upload(request: PdfUploadInitRequest) -> dict:
    filename = Path(request.filename).name
    if Path(filename).suffix.lower() != ".pdf" and request.contentType != "application/pdf":
        raise HTTPException(status_code=415, detail="分块上传目前只用于 PDF")
    expected_chunks = math.ceil(request.size / request.chunkSize)
    if expected_chunks != request.totalChunks:
        raise HTTPException(status_code=400, detail="文件大小与分块数量不一致")

    upload_id = uuid.uuid4().hex
    directory = UPLOAD_ROOT / upload_id
    directory.mkdir(parents=True, exist_ok=False)
    job: dict[str, Any] = {
        "uploadId": upload_id,
        "filename": filename,
        "contentType": request.contentType,
        "size": request.size,
        "chunkSize": request.chunkSize,
        "totalChunks": request.totalChunks,
        "sourceText": request.sourceText,
        "directory": directory,
        "status": "uploading",
        "progress": 0,
        "message": "等待上传 PDF 分块",
        "startedAt": time.time(),
        "updatedAt": time.time(),
        "result": None,
    }
    pdf_uploads[upload_id] = job
    store.save_job(job)
    return upload_status(job)


@app.put("/api/uploads/{upload_id}/chunks/{index}")
async def upload_pdf_chunk(upload_id: str, index: int, request: Request) -> dict:
    job = upload_job(upload_id)
    if job["status"] == "complete":
        raise HTTPException(status_code=409, detail="这个上传任务已经完成")
    if index < 0 or index >= job["totalChunks"]:
        raise HTTPException(status_code=400, detail="分块序号超出范围")

    content = await request.body()
    expected_size = job["chunkSize"]
    if index == job["totalChunks"] - 1:
        expected_size = job["size"] - job["chunkSize"] * index
    if len(content) != expected_size:
        raise HTTPException(
            status_code=400,
            detail=f"第 {index + 1} 块大小不正确：收到 {len(content)}，应为 {expected_size}",
        )

    chunk_path = job["directory"] / f"chunk-{index:06d}.part"
    chunk_path.write_bytes(content)
    uploaded_count = len(list(job["directory"].glob("chunk-*.part")))
    update_upload_job(
        job,
        "uploading",
        round(uploaded_count / job["totalChunks"] * 20),
        f"已上传 {uploaded_count}/{job['totalChunks']} 个分块",
    )
    return {
        "uploadId": upload_id,
        "index": index,
        "received": len(content),
        "uploadedCount": uploaded_count,
    }


@app.get("/api/uploads/{upload_id}/status")
def get_pdf_upload_status(upload_id: str) -> dict:
    return upload_status(upload_job(upload_id))


@app.post("/api/uploads/{upload_id}/complete")
def complete_pdf_upload(upload_id: str) -> dict:
    job = upload_job(upload_id)
    if job["status"] == "complete" and job.get("result"):
        return job["result"]

    chunk_paths = [
        job["directory"] / f"chunk-{index:06d}.part"
        for index in range(job["totalChunks"])
    ]
    missing = [index for index, path in enumerate(chunk_paths) if not path.exists()]
    if missing:
        raise HTTPException(status_code=409, detail=f"仍缺少 {len(missing)} 个分块")

    update_upload_job(job, "merging", 22, "正在合并分块并计算文件校验值")
    source_path = job["directory"] / "source.pdf"
    digest = hashlib.sha256()
    written = 0
    with source_path.open("wb") as merged:
        for chunk_path in chunk_paths:
            with chunk_path.open("rb") as chunk:
                while block := chunk.read(1024 * 1024):
                    merged.write(block)
                    digest.update(block)
                    written += len(block)
    if written != job["size"]:
        update_upload_job(job, "failed", 22, "合并后的 PDF 大小校验失败")
        raise HTTPException(status_code=400, detail="合并后的 PDF 大小校验失败")

    update_upload_job(job, "validating", 28, "文件合并完成，正在读取 PDF 页数")
    try:
        validate_pdf_envelope(source_path)
        try:
            page_count = ocr_runtime.page_count(source_path)
        except Exception:
            page_count = len(PdfReader(str(source_path)).pages)
        if page_count == 0:
            raise ValueError("PDF 没有页面")
    except Exception as error:
        update_upload_job(job, "failed", 28, f"PDF 校验失败：{error}")
        raise HTTPException(status_code=422, detail=f"PDF 无法解析：{error}") from error

    update_upload_job(job, "splitting", 35, f"校验完成，共 {page_count} 页；正在规划处理批次")
    batches = []
    for start in range(0, page_count, PDF_BATCH_PAGES):
        end = min(start + PDF_BATCH_PAGES, page_count)
        batch_id = len(batches) + 1
        batches.append(
            {
                "id": f"batch-{batch_id:03d}",
                "startPage": start + 1,
                "endPage": end,
                "pageCount": end - start,
                "status": "processed" if batch_id == 1 else "queued",
            }
        )

    update_upload_job(
        job,
        "splitting",
        50,
        f"已规划 {len(batches)} 个批次；无需复制整本 PDF",
    )

    for chunk_path in chunk_paths:
        chunk_path.unlink(missing_ok=True)

    # MinerU 直接通过 --start/--end 读取原 PDF 的前五页。其余批次只保存页码范围，
    # 不再用 pypdf 打开整本教材或复制页面，避免图片型 PDF 在分页阶段等待一分钟。
    first_batch = batches[0]
    extracted_text = ""
    if not job.get("sourceText", "").strip() and not ocr_runtime.should_use_mineru():
        try:
            extracted_text = extract_pdf_text(PdfReader(str(source_path)), max_pages=PDF_BATCH_PAGES)
        except Exception:
            extracted_text = ""

    preview_pages = first_batch["pageCount"]
    update_upload_job(
        job,
        "ocr",
        55,
        f"MinerU 正在识别首批 {preview_pages} 页；整本 {page_count} 页无需等待",
    )
    lesson_source, ocr_run = resolve_ocr_text(
        job.get("sourceText", ""),
        extracted_text,
        source_path,
        0,
        preview_pages - 1,
        job["directory"] / "assets" / first_batch["id"],
        f"/api/uploads/{upload_id}/assets/{first_batch['id']}",
    )
    update_upload_job(job, "generating", 88, "首批内容已提取，正在按题号拆分并生成课程")
    question_sources = limited_question_sources(lesson_source)
    asset_dir = job["directory"] / "assets" / first_batch["id"]
    write_model_prompt_artifact(asset_dir, question_sources)
    ocr_run["sourceArtifactUrl"] = f"/api/uploads/{upload_id}/artifacts/{first_batch['id']}/source.md"
    ocr_run["promptArtifactUrl"] = f"/api/uploads/{upload_id}/artifacts/{first_batch['id']}/model-prompt.md"
    payloads, guide_cards_list, model_runs, review_runs = process_question_sources(
        question_sources,
        first_batch,
        ocr_run,
        asset_dir,
        job,
    )
    payload = payloads[0]
    guide_cards = guide_cards_list[0]
    model_run = model_runs[0]
    review_run = review_runs[0]
    question_keys = [payload["question"]["sourceQuestionKey"] for payload in payloads]
    result = {
        "uploadId": upload_id,
        "importId": f"pdf-{digest.hexdigest()[:12]}",
        "filename": job["filename"],
        "contentType": "application/pdf",
        "size": job["size"],
        "stored": True,
        "temporary": True,
        "modelRun": model_run,
        "modelRuns": model_runs,
        "ocrRun": ocr_run,
        "reviewRun": review_run,
        "reviewRuns": review_runs,
        "stages": [
            {"id": "upload", "label": "分块上传", "status": "done"},
            {"id": "merge", "label": "PDF 合并与校验", "status": "done"},
            {"id": "split", "label": "按页规划批次", "status": "done"},
            {"id": "ocr", "label": f"首批 {preview_pages} 页 MinerU OCR", "status": "done"},
            {"id": "guides", "label": "结构化与引导卡", "status": "done"},
        ],
        "extraction": {
            "chapter": payload["question"]["chapter"],
            "knowledgePoint": payload["question"]["knowledgePoint"],
            "questionCount": len(payloads),
            "questionLimit": MAX_QUESTIONS_PER_BATCH,
            "formulaCount": sum(block.count("$") // 2 for _, block, _ in question_sources),
            "guideCardCount": sum(len(cards) for cards in guide_cards_list),
            "pageCount": page_count,
            "batchCount": len(batches),
            "confidence": 0.96,
            "mode": f"model-from-{ocr_run['provider']}" if lesson_source else "demo-seed-no-ocr",
        },
        "batches": batches,
        "questionPayload": payload,
        "questionPayloads": payloads,
        "batchQuestionKeys": {first_batch["id"]: question_keys},
    }
    job["batchPayloads"] = dict(zip(question_keys, payloads))
    job["batchGuideCards"] = dict(zip(question_keys, guide_cards_list))
    job["batchQuestionKeys"] = {first_batch["id"]: question_keys}
    job["result"] = result
    job["completedAt"] = time.time()
    store.save_questions(
        upload_id,
        list(zip(question_keys, payloads, guide_cards_list)),
    )
    update_upload_job(job, "complete", 100, f"首批 {preview_pages} 页已拆分为 {len(payloads)} 道题，其余批次可按需处理")
    return result


@app.post("/api/help", response_model=TutorReply)
def get_help(request: HelpRequest) -> TutorReply:
    return generate_model_reply(request)


@app.post("/api/uploads/{upload_id}/batches/{batch_id}/process")
def process_pdf_batch(upload_id: str, batch_id: str, force: bool = False) -> dict:
    """OCR one queued five-page range and add its generated exercise to the bank."""
    job = upload_job(upload_id)
    result = job.get("result")
    if job.get("status") != "complete" or not result:
        raise HTTPException(status_code=409, detail="请先完成教材首批处理")

    batch = next((item for item in result.get("batches", []) if item["id"] == batch_id), None)
    if not batch:
        raise HTTPException(status_code=404, detail="没有找到这个教材批次")

    batch_question_keys = job.setdefault("batchQuestionKeys", {}).get(batch_id, [])
    stored_payloads = [job.setdefault("batchPayloads", {})[key] for key in batch_question_keys if key in job["batchPayloads"]]
    stored_payload = stored_payloads[0] if stored_payloads else job.setdefault("batchPayloads", {}).get(batch_id)
    if stored_payload and not force:
        return {
            "batch": batch,
            "questionPayload": stored_payload,
            "questionPayloads": stored_payloads or [stored_payload],
            "ocrRun": result["ocrRun"],
            "modelRun": stored_payload["modelRun"],
        }

    processing = job.setdefault("processingBatches", set())
    if batch_id in processing:
        raise HTTPException(status_code=409, detail="这个批次正在处理中")
    processing.add(batch_id)
    try:
        source_path = job["directory"] / "source.pdf"
        start_page = batch["startPage"] - 1
        end_page = batch["endPage"] - 1
        ocr_start_page = max(0, start_page - 1)
        asset_dir = job["directory"] / "assets" / batch_id
        cached_markdown = asset_dir / "source.md"
        extracted_text = ""
        if not ocr_runtime.should_use_mineru():
            reader = PdfReader(str(source_path))
            pages = []
            for page_index in range(ocr_start_page, end_page + 1):
                text = (reader.pages[page_index].extract_text() or "").strip()
                if text:
                    pages.append(text)
            extracted_text = "\n\n".join(pages)[:16_000]

        if cached_markdown.is_file():
            lesson_source = cached_markdown.read_text(encoding="utf-8", errors="replace")[:40_000]
            image_urls = [
                f"/api/uploads/{upload_id}/assets/{batch_id}/{path.name}"
                for path in sorted(asset_dir.iterdir())
                if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
            ]
            ocr_run = {
                "requestedProvider": ocr_runtime.selection.provider,
                "provider": "mineru",
                "mode": "persisted-markdown",
                "fallback": False,
                "output": "markdown",
                "startPage": ocr_start_page + 1,
                "endPage": end_page + 1,
                "imageUrls": image_urls,
            }
        else:
            lesson_source, ocr_run = resolve_ocr_text(
                "",
                extracted_text,
                source_path,
                ocr_start_page,
                end_page,
                asset_dir,
                f"/api/uploads/{upload_id}/assets/{batch_id}",
            )
        context_note = (
            f"\n\n[页码说明：识别内容来自第 {ocr_start_page + 1}-{end_page + 1} 页；"
            f"目标批次为第 {start_page + 1}-{end_page + 1} 页。前一页只用于补齐跨页题干。]\n"
        )
        question_sources = limited_question_sources(lesson_source)
        if not split_question_sources(lesson_source):
            question_sources = [("", context_note + lesson_source, MARKDOWN_IMAGE_PATTERN.findall(lesson_source))]
        write_model_prompt_artifact(asset_dir, question_sources)
        ocr_run["sourceArtifactUrl"] = f"/api/uploads/{upload_id}/artifacts/{batch_id}/source.md"
        ocr_run["promptArtifactUrl"] = f"/api/uploads/{upload_id}/artifacts/{batch_id}/model-prompt.md"
        payloads, guide_cards_list, model_runs, review_runs = process_question_sources(
            question_sources,
            batch,
            ocr_run,
            asset_dir,
            job,
        )
        payload = payloads[0]
        guide_cards = guide_cards_list[0]
        model_run = model_runs[0]
        review_run = review_runs[0]
        question_keys = [item["question"]["sourceQuestionKey"] for item in payloads]
        batch["status"] = "processed"
        store.save_questions(
            upload_id,
            list(zip(question_keys, payloads, guide_cards_list)),
        )
        for key, item, cards in zip(question_keys, payloads, guide_cards_list):
            job["batchPayloads"][key] = item
            job.setdefault("batchGuideCards", {})[key] = cards
        job.setdefault("batchQuestionKeys", {})[batch_id] = question_keys
        result.setdefault("batchQuestionKeys", {})[batch_id] = question_keys
        result.setdefault("questionPayloads", [])
        result["questionPayloads"] = [
            item for key in sorted(job["batchPayloads"]) for item in [job["batchPayloads"][key]]
        ]
        result["questionPayload"] = result["questionPayloads"][0]
        result["extraction"]["questionCount"] = len(result["questionPayloads"])
        result["extraction"]["guideCardCount"] = sum(
            len(cards) for cards in job.get("batchGuideCards", {}).values()
        )
        update_upload_job(
            job,
            "complete",
            100,
            f"批次 {batch['startPage']}-{batch['endPage']} 页已更新 {len(payloads)} 道题",
        )
        return {
            "batch": batch,
            "questionPayload": payload,
            "questionPayloads": payloads,
            "ocrRun": ocr_run,
            "modelRun": model_run,
            "modelRuns": model_runs,
            "reviewRun": review_run,
            "reviewRuns": review_runs,
        }
    except HTTPException as error:
        batch["status"] = "failed"
        batch["error"] = str(error.detail)
        update_upload_job(job, "complete", 100, f"批次处理失败，已保留原题：{error.detail}")
        raise
    except Exception as error:
        batch["status"] = "failed"
        batch["error"] = str(error)
        update_upload_job(job, "complete", 100, f"批次处理失败，已保留原题：{error}")
        raise HTTPException(status_code=422, detail=f"批次处理失败：{error}") from error
    finally:
        processing.discard(batch_id)


@app.get("/api/uploads/{upload_id}/assets/{batch_id}/{filename}")
def get_pdf_asset(upload_id: str, batch_id: str, filename: str) -> FileResponse:
    job = upload_job(upload_id)
    asset_root = (job["directory"] / "assets" / batch_id).resolve()
    asset_path = (asset_root / Path(filename).name).resolve()
    if asset_path.parent != asset_root or not asset_path.is_file():
        raise HTTPException(status_code=404, detail="题目图片不存在")
    if asset_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(status_code=415, detail="不支持的题目资源类型")
    return FileResponse(asset_path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/api/uploads/{upload_id}/artifacts/{batch_id}/{filename}")
def get_pdf_artifact(upload_id: str, batch_id: str, filename: str) -> FileResponse:
    job = upload_job(upload_id)
    artifact_root = (job["directory"] / "assets" / batch_id).resolve()
    safe_name = Path(filename).name
    if safe_name not in {"source.md", "model-prompt.md"}:
        raise HTTPException(status_code=404, detail="中间产物不存在")
    artifact_path = (artifact_root / safe_name).resolve()
    if artifact_path.parent != artifact_root or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="中间产物不存在")
    return FileResponse(
        artifact_path,
        media_type="text/markdown; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/library")
def list_textbook_library() -> dict:
    return {"items": store.list_imports()}


@app.get("/api/library/{upload_id}")
def get_textbook_library_item(upload_id: str) -> dict:
    job = upload_job(upload_id)
    result = job.get("result")
    if job.get("status") != "complete" or not result:
        raise HTTPException(status_code=409, detail="这本教材尚未处理完成")
    restored = dict(result)
    def question_sort_key(item: dict[str, Any]) -> tuple[int, str]:
        number = str(item.get("question", {}).get("questionNumber", ""))
        match = re.search(r"\d+", number)
        return (int(match.group()) if match else 10**9, number)

    payloads = sorted(job.get("batchPayloads", {}).values(), key=question_sort_key)
    restored["questionPayloads"] = payloads or [result["questionPayload"]]
    restored["questionPayload"] = restored["questionPayloads"][0]
    return restored
