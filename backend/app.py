from __future__ import annotations

import hashlib
import math
import re
import shutil
import tempfile
import time
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pypdf import PdfReader

from model_runtime import runtime
from ocr_runtime import runtime as ocr_runtime
from review_runtime import runtime_reviewer
from storage import store
from runtime_routes import build_runtime_router
from learning_routes import build_learning_router
from lesson_contracts import lesson_document_from_payload
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
from observability import log_event
from application import create_app
from upload_registry import UploadRegistry
from tutor_checks import build_reply, equation_conflict, equivalent_linear_equations, mock_model_run
from tutor_engine import TutorEngine


app = create_app()


ALLOWED_UPLOAD_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif",
    ".gif", ".bmp", ".tif", ".tiff", ".pdf",
}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
PDF_MAX_UPLOAD_BYTES = 500 * 1024 * 1024
PDF_CHUNK_BYTES = 5 * 1024 * 1024
PDF_BATCH_PAGES = 5
PDF_TAIL_CHECK_BYTES = 64 * 1024
lesson_store: dict[str, dict[str, Any]] = {}
UPLOAD_ROOT = store.upload_root
upload_registry = UploadRegistry(
    store=store,
    lesson_store=lesson_store,
    default_guide_cards=GUIDE_CARDS,
    pdf_tail_check_bytes=PDF_TAIL_CHECK_BYTES,
)
pdf_uploads = upload_registry.uploads
upload_job = upload_registry.get
upload_status = upload_registry.status
update_upload_job = upload_registry.update
validate_pdf_envelope = upload_registry.validate_pdf_envelope
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
app.include_router(build_learning_router(store=store))


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
        log_event("ocr.completed", provider="manual", mode="pasted-text", fallback=False)
        return source_text.strip(), {
            "requestedProvider": "manual",
            "provider": "manual",
            "mode": "pasted-text",
            "fallback": False,
            "output": "text",
        }

    requested = ocr_runtime.selection.provider
    if source_path and ocr_runtime.should_use_mineru():
        log_event(
            "ocr.started",
            provider=requested,
            start_page=start_page + 1,
            end_page=None if end_page is None else end_page + 1,
        )
        try:
            result = ocr_runtime.parse(
                source_path,
                start_page,
                end_page,
                asset_dir,
                asset_url_prefix,
            )
            log_event("ocr.completed", provider=result[1].get("provider"), fallback=False)
            return result
        except Exception as error:
            log_event(
                "ocr.failed",
                level=40,
                provider=requested,
                fallback=bool(extracted_text),
                error_type=type(error).__name__,
                error=str(error)[:300],
                exc_info=True,
            )
            return extracted_text, {
                "requestedProvider": requested,
                "provider": "pypdf" if extracted_text else "none",
                "mode": "text-layer-fallback" if extracted_text else "ocr-failed",
                "fallback": True,
                "error": str(error),
                "output": "text",
            }

    log_event(
        "ocr.completed",
        provider="pypdf" if extracted_text else "none",
        mode="text-layer" if extracted_text else "no-text-layer",
        fallback=False,
    )
    return extracted_text, {
        "requestedProvider": requested,
        "provider": "pypdf" if extracted_text else "none",
        "mode": "text-layer" if extracted_text else "no-text-layer",
        "fallback": False,
        "output": "text",
    }


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
    log_event("question.batch.started", question_count=len(question_sources), batch_id=batch.get("id"))
    for index, (number, block, images) in enumerate(question_sources):
        log_event(
            "question.started",
            batch_id=batch.get("id"),
            question_number=number or index + 1,
            image_count=len(images),
        )
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
        log_event(
            "question.completed",
            batch_id=batch.get("id"),
            question_number=number or index + 1,
            question_type=payload.get("question", {}).get("questionType"),
            model_provider=model_run.get("provider"),
            review_provider=review_run.get("provider"),
        )
    log_event("question.batch.completed", question_count=len(payloads), batch_id=batch.get("id"))
    return payloads, guide_cards_list, model_runs, review_runs


def generate_lesson(source_text: str) -> tuple[dict, list[dict[str, Any]], dict[str, Any]]:
    selection = runtime.selection
    started = time.perf_counter()
    log_event("model.generation.started", provider=selection.provider, model=selection.model)
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
        log_event("model.generation.completed", provider="mock", duration_ms=round((time.perf_counter() - started) * 1000, 1))
        return payload, GUIDE_CARDS, run

    try:
        generated, run = runtime.generate_json(prompt, LESSON_SCHEMA, max_tokens=1600)
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


tutor_engine = TutorEngine(lesson_store=lesson_store, runtime=runtime, guide_cards=GUIDE_CARDS)


def generate_model_reply(request: HelpRequest) -> TutorReply:
    """Compatibility wrapper for routes and existing tests."""
    return tutor_engine.reply(request)


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

    log_event(
        "textbook.import.started",
        filename=filename,
        content_type=content_type or "unknown",
        size_bytes=len(content),
    )

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
    log_event(
        "textbook.import.completed",
        filename=filename,
        size_bytes=len(content),
        ocr_provider=ocr_run.get("provider"),
        model_provider=model_run.get("provider"),
    )
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
    log_event(
        "upload.initialized",
        upload_id=upload_id,
        filename=filename,
        size_bytes=request.size,
        total_chunks=request.totalChunks,
    )
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
    log_event(
        "upload.chunk.received",
        level=10,
        upload_id=upload_id,
        chunk_index=index,
        chunk_bytes=len(content),
        uploaded_chunks=uploaded_count,
        total_chunks=job["totalChunks"],
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
    log_event("upload.processing.started", upload_id=upload_id, filename=job.get("filename"))
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
        log_event(
            "upload.processing.failed",
            level=40,
            upload_id=upload_id,
            stage="pdf-validation",
            error_type=type(error).__name__,
            error=str(error)[:300],
            exc_info=True,
        )
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
    for item, cards in zip(payloads, guide_cards_list):
        store.save_lesson(lesson_document_from_payload(
            item,
            source_upload_id=upload_id,
            guide_cards=cards,
        ))
    update_upload_job(job, "complete", 100, f"首批 {preview_pages} 页已拆分为 {len(payloads)} 道题，其余批次可按需处理")
    log_event(
        "upload.processing.completed",
        upload_id=upload_id,
        page_count=page_count,
        batch_count=len(batches),
        question_count=len(payloads),
        ocr_provider=ocr_run.get("provider"),
    )
    return result


@app.post("/api/help", response_model=TutorReply)
def get_help(request: HelpRequest) -> TutorReply:
    started = time.perf_counter()
    reply = generate_model_reply(request)
    log_event(
        "help.completed",
        question_id=request.questionId,
        mode=request.mode,
        source=reply.source,
        assessment=reply.guideContext.get("assessment"),
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
    )
    return reply


@app.post("/api/uploads/{upload_id}/batches/{batch_id}/process")
def process_pdf_batch(upload_id: str, batch_id: str, force: bool = False) -> dict:
    """OCR one queued five-page range and add its generated exercise to the bank."""
    job = upload_job(upload_id)
    log_event("upload.batch.started", upload_id=upload_id, batch_id=batch_id, force=force)
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
        for item, cards in zip(payloads, guide_cards_list):
            store.save_lesson(lesson_document_from_payload(
                item,
                source_upload_id=upload_id,
                guide_cards=cards,
            ))
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
        log_event(
            "upload.batch.completed",
            upload_id=upload_id,
            batch_id=batch_id,
            question_count=len(payloads),
            ocr_provider=ocr_run.get("provider"),
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
        log_event(
            "upload.batch.failed",
            level=40,
            upload_id=upload_id,
            batch_id=batch_id,
            error_type=type(error).__name__,
            error=str(error.detail)[:300],
            exc_info=True,
        )
        raise
    except Exception as error:
        batch["status"] = "failed"
        batch["error"] = str(error)
        update_upload_job(job, "complete", 100, f"批次处理失败，已保留原题：{error}")
        log_event(
            "upload.batch.failed",
            level=40,
            upload_id=upload_id,
            batch_id=batch_id,
            error_type=type(error).__name__,
            error=str(error)[:300],
            exc_info=True,
        )
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
