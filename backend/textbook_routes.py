"""教材导入、分块上传与持久化资源的 HTTP 边界。

耗时 PDF 流程位于 :mod:`textbook_processing`。本模块只处理协议与传输校验，便于区分哪些规则必须在
接收请求前执行、哪些步骤将来可以原样迁移到后台 Worker。
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

# HTTP 边界的体积限制必须在 OCR/模型调用前保护内存与磁盘；生成数量限制属于处理服务与题目流水线，
# 两类限制刻意分开，避免传输细节渗透进领域逻辑。
ALLOWED_UPLOAD_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif",
    ".gif", ".bmp", ".tif", ".tiff", ".pdf",
}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
PDF_MAX_UPLOAD_BYTES = 500 * 1024 * 1024
PDF_CHUNK_BYTES = 5 * 1024 * 1024
PDF_TAIL_CHECK_BYTES = 64 * 1024
UPLOAD_ROOT = store.upload_root

# Registry 是 HTTP 轮询与持久化任务之间的小型状态边界。Route 和 Service 共享同一实例，
# 因而都能看到从数据库恢复的任务，而不是维护两份进度。
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

# 兼容旧测试和 ASGI 组合根的导出；新流程应优先依赖 upload_registry 或 processing_service。
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

    # MinerU 只能接收真实文件路径；手工文本和 PDF 文字层回退无需写入临时目录。
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
    if request.publicationId:
        publication = store.load_publication(request.publicationId)
        if not publication or publication.get("status") != "published":
            raise HTTPException(status_code=404, detail="已发布互动试卷不存在")
        lesson = next((
            item for item in publication.get("lessons", [])
            if (item.get("questionPayload") or {}).get("question", {}).get("id") == request.questionId
        ), None)
        if not lesson:
            raise HTTPException(status_code=404, detail="互动试卷题目不存在")
        # 进程重启后内存课程缓存为空。学生答题时从已发布快照恢复准确上下文，
        # 避免错误地使用 Demo 默认几何题的引导卡。
        payload = lesson.get("questionPayload") or {}
        question = payload.get("question") or {}
        guide_cards = lesson.get("guideCards") or [{
            "level": 0,
            "stuckAt": "需要从题目条件中找到下一步。",
            "knowledge": [question.get("knowledgePoint") or "当前知识点"],
            "hint": "先圈出题目给出的已知量，再判断要使用的关系。",
            "question": "题目已经给出了哪些量，要求你求什么？",
            "canvasAction": "show-base",
        }]
        lesson_store[request.questionId] = {"payload": payload, "guideCards": guide_cards}
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
def process_pdf_batch(
    upload_id: str,
    batch_id: str,
    force: bool = False,
    refreshOcr: bool = False,
) -> dict[str, Any]:
    """Delegate one queued page range to the reusable processing service."""
    return processing_service.process_batch(upload_id, batch_id, force, refresh_ocr=refreshOcr)


@router.post("/api/uploads/{upload_id}/questions/{question_source_key}/regenerate")
def regenerate_question(
    upload_id: str,
    question_source_key: str,
    refreshOcr: bool = False,
) -> dict[str, Any]:
    """只修复一题；需要重新识别页面时由调用方显式传 refreshOcr。"""
    return processing_service.regenerate_question(
        upload_id,
        question_source_key,
        refresh_ocr=refreshOcr,
    )


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
