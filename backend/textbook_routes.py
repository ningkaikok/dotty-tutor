"""HTTP boundary for textbook imports, multipart uploads and stored assets.

Long-running PDF orchestration lives in :mod:`textbook_processing`. Keeping
this module focused on HTTP makes it easy to see which validation belongs to
the transport layer and which work could later run in a background worker.
"""

from __future__ import annotations

import hashlib
import math
import tempfile
import time
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pypdf import PdfReader

from lesson_generation import generate_lesson, generate_model_reply, lesson_store
from library_routes import build_library_router
from observability import log_event
from ocr_runtime import runtime as ocr_runtime
from question_contracts import GUIDE_CARDS, HelpRequest, PdfUploadInitRequest, TutorReply
from storage import store
from textbook_ocr import extract_pdf_text, resolve_ocr_text
from textbook_processing import PDF_BATCH_PAGES, TextbookProcessingService
from upload_registry import UploadRegistry


router = APIRouter()

# Limits at the HTTP boundary protect memory and disk before any OCR/model work
# starts. They are deliberately separate from generation limits, which belong
# to the processing service and question pipeline.
ALLOWED_UPLOAD_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif",
    ".gif", ".bmp", ".tif", ".tiff", ".pdf",
}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
PDF_MAX_UPLOAD_BYTES = 500 * 1024 * 1024
PDF_CHUNK_BYTES = 5 * 1024 * 1024
PDF_TAIL_CHECK_BYTES = 64 * 1024
UPLOAD_ROOT = store.upload_root

# The registry is the small state boundary between HTTP polling and durable
# storage. The service receives the same instance, so both see restored jobs.
upload_registry = UploadRegistry(
    store=store,
    lesson_store=lesson_store,
    default_guide_cards=GUIDE_CARDS,
    pdf_tail_check_bytes=PDF_TAIL_CHECK_BYTES,
)
processing_service = TextbookProcessingService(
    store=store,
    upload_registry=upload_registry,
    ocr_runtime=ocr_runtime,
)

# Compatibility exports used by existing tests and the ASGI composition root.
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
    """Read one page and return a lesson without persisting the source file."""
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
            extracted_text = extract_pdf_text(PdfReader(BytesIO(content)))
        except Exception as error:
            raise HTTPException(status_code=422, detail=f"PDF 无法解析：{error}") from error

    # MinerU requires a real file path. Manual OCR text and PDF text-layer
    # fallback can skip the temporary file entirely.
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
    """Create a content-addressed multipart PDF upload job."""
    filename = Path(request.filename).name
    if Path(filename).suffix.lower() != ".pdf" and request.contentType != "application/pdf":
        raise HTTPException(status_code=415, detail="分块上传目前只用于 PDF")
    expected_chunks = math.ceil(request.size / request.chunkSize)
    if expected_chunks != request.totalChunks:
        raise HTTPException(status_code=400, detail="文件大小与分块数量不一致")

    upload_id = uuid.uuid4().hex
    directory = UPLOAD_ROOT / upload_id
    directory.mkdir(parents=True, exist_ok=False)
    now = time.time()
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
        "startedAt": now,
        "updatedAt": now,
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
def complete_pdf_upload(upload_id: str) -> dict[str, Any]:
    """Delegate merge, OCR and first-batch generation to the application service."""
    return processing_service.complete_upload(upload_id)


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
def process_pdf_batch(upload_id: str, batch_id: str, force: bool = False) -> dict[str, Any]:
    """Delegate one queued page range to the reusable processing service."""
    return processing_service.process_batch(upload_id, batch_id, force)


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
    return FileResponse(
        asset_path,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


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


__all__ = [
    "PDF_BATCH_PAGES",
    "complete_pdf_upload",
    "pdf_uploads",
    "process_pdf_batch",
    "router",
]
