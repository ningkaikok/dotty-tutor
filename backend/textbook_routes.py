"""FastAPI routes for the textbook import and tutoring workflow.

The module is intentionally the only HTTP-facing textbook boundary. OCR, model
generation, persistence, and upload bookkeeping live in dedicated adapters.
"""

from __future__ import annotations

import hashlib
import math
import shutil
import tempfile
import time
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pypdf import PdfReader

from lesson_contracts import lesson_document_from_payload
from library_routes import build_library_router
from lesson_generation import (
    generate_lesson,
    generate_model_reply,
    lesson_store,
)
from observability import log_event
from ocr_runtime import runtime as ocr_runtime
from question_contracts import GUIDE_CARDS, HelpRequest, PdfUploadInitRequest, TutorReply
from question_pipeline import write_model_prompt_artifact
from question_processing import process_question_sources
from question_source import (
    MARKDOWN_IMAGE_PATTERN,
    MAX_QUESTIONS_PER_BATCH,
    limited_question_sources,
    question_key,
    split_question_sources,
)
from storage import store
from textbook_ocr import extract_pdf_text, resolve_ocr_text
from upload_registry import UploadRegistry


router = APIRouter()

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


@router.post("/api/textbook/import")
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


@router.post("/api/uploads/init")
def init_pdf_upload(request: PdfUploadInitRequest) -> dict:
    """Create or resume a content-addressed multipart PDF upload."""
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


@router.put("/api/uploads/{upload_id}/chunks/{index}")
async def upload_pdf_chunk(upload_id: str, index: int, request: Request) -> dict:
    """Persist exactly one bounded chunk after validating its expected size."""
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


@router.get("/api/uploads/{upload_id}/status")
def get_pdf_upload_status(upload_id: str) -> dict:
    """Return a serializable progress snapshot for frontend polling."""
    return upload_status(upload_job(upload_id))


@router.post("/api/uploads/{upload_id}/complete")
def complete_pdf_upload(upload_id: str) -> dict:
    """Merge, validate and process the first page batch synchronously.

    This is the main MVP architecture debt: production should enqueue the OCR
    work after merge and return a task identifier instead of holding the HTTP
    request open.
    """
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

    # Reject a re-upload of identical content before spending OCR/generation on
    # it. The content SHA-256 is carried in importId, so a completed match means
    # the same textbook already exists in the library.
    content_import_id = f"pdf-{digest.hexdigest()[:12]}"
    existing = store.find_completed_import(content_import_id, exclude_upload_id=upload_id)
    if existing:
        update_upload_job(job, "duplicate", 22, f"内容与已有教材重复：{existing['filename']}")
        shutil.rmtree(job["directory"], ignore_errors=True)
        pdf_uploads.pop(upload_id, None)
        log_event(
            "upload.duplicate.rejected",
            upload_id=upload_id,
            duplicate_of=existing["uploadId"],
            filename=job.get("filename"),
        )
        raise HTTPException(
            status_code=409,
            detail=f"这本教材已存在（{existing['filename']}），请在教材库中打开，或删除后再重新上传。",
        )

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
        update_upload_job,
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


@router.post("/api/help", response_model=TutorReply)
def get_help(request: HelpRequest) -> TutorReply:
    """Generate one answer/help turn and emit assessment telemetry."""
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


@router.post("/api/uploads/{upload_id}/batches/{batch_id}/process")
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
            update_upload_job,
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


@router.get("/api/uploads/{upload_id}/assets/{batch_id}/{filename}")
def get_pdf_asset(upload_id: str, batch_id: str, filename: str) -> FileResponse:
    """Serve only raster assets located inside the requested batch directory."""
    job = upload_job(upload_id)
    asset_root = (job["directory"] / "assets" / batch_id).resolve()
    asset_path = (asset_root / Path(filename).name).resolve()
    if asset_path.parent != asset_root or not asset_path.is_file():
        raise HTTPException(status_code=404, detail="题目图片不存在")
    if asset_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(status_code=415, detail="不支持的题目资源类型")
    return FileResponse(asset_path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/api/uploads/{upload_id}/artifacts/{batch_id}/{filename}")
def get_pdf_artifact(upload_id: str, batch_id: str, filename: str) -> FileResponse:
    """Expose a small allowlist of review artifacts without directory access."""
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


router.include_router(build_library_router(
    store=store,
    upload_registry=upload_registry,
    lesson_store=lesson_store,
))
