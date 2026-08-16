"""HTTP routes for image capture, confirmation and the personal mistake book."""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from domain.contracts.mistake import MistakeArchiveRequest, MistakeConfirmation
from observability import log_event


MAX_MISTAKE_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif", ".bmp", ".tif", ".tiff"}
ALLOWED_IMAGE_MIME_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
    "image/gif", "image/bmp", "image/tiff",
}
RecognizeMistake = Callable[[Path, str, Path, str], tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]]]
ArchiveCleanup = Callable[[str, str], int | None]


def build_mistake_router(
    *, store: Any, recognize: RecognizeMistake,
    archive_cleanup: ArchiveCleanup | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/mistakes", tags=["mistakes"])

    @router.post("/import")
    async def import_mistake(
        file: UploadFile = File(...),
        sourceText: str = Form(default="", max_length=20_000),
        originalAnswer: str = Form(default="", max_length=2_000),
        learnerId: str = Form(default="local-demo", min_length=1, max_length=128),
    ) -> dict[str, Any]:
        filename = Path(file.filename or "mistake-image").name
        suffix = Path(filename).suffix.lower()
        content_type = (file.content_type or "").lower()
        if content_type not in ALLOWED_IMAGE_MIME_TYPES and suffix not in ALLOWED_IMAGE_SUFFIXES:
            raise HTTPException(status_code=415, detail="错题录入目前只支持单张图片")
        content = await file.read(MAX_MISTAKE_IMAGE_BYTES + 1)
        await file.close()
        if not content:
            raise HTTPException(status_code=400, detail="上传图片不能为空")
        if len(content) > MAX_MISTAKE_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="错题图片不能超过 10 MB")

        mistake_id = uuid.uuid4().hex
        directory = store.item_directory(mistake_id)
        source_path = directory / f"source{suffix or '.jpg'}"
        source_path.write_bytes(content)
        asset_directory = directory / "assets"
        asset_prefix = f"/api/mistakes/{mistake_id}/assets"
        log_event("mistake.import.started", mistake_id=mistake_id, filename=filename, size_bytes=len(content))
        try:
            payload, guide_cards, ocr_run, model_run = recognize(
                source_path,
                sourceText,
                asset_directory,
                asset_prefix,
            )
        except Exception as error:
            log_event(
                "mistake.import.failed",
                level=40,
                mistake_id=mistake_id,
                error_type=type(error).__name__,
                error=str(error)[:300],
                exc_info=True,
            )
            shutil.rmtree(directory, ignore_errors=True)
            raise HTTPException(status_code=422, detail=f"错题识别失败：{error}") from error

        question = payload.get("question", {})
        now = time.time()
        item = store.create({
            "mistakeId": mistake_id,
            "learnerId": learnerId,
            "sourceFilename": filename,
            "contentType": content_type or "application/octet-stream",
            "sourceImagePath": str(source_path),
            "sourceImageUrl": f"/api/mistakes/{mistake_id}/source",
            "questionPayload": payload,
            "guideCards": guide_cards,
            "ocrRun": ocr_run,
            "modelRun": model_run,
            "originalAnswer": originalAnswer,
            "subject": "数学",
            "gradeBand": "初中",
            "chapter": str(question.get("chapter") or "待确认"),
            "knowledgePoint": str(question.get("knowledgePoint") or "待确认"),
            "status": "pending_confirmation",
            "createdAt": now,
            "updatedAt": now,
        })
        log_event(
            "mistake.import.completed",
            mistake_id=mistake_id,
            ocr_provider=ocr_run.get("provider"),
            model_provider=model_run.get("provider"),
        )
        return _public_item(item)

    @router.get("")
    def list_mistakes(learnerId: str = "local-demo", includeArchived: bool = False) -> dict[str, Any]:
        return {"learnerId": learnerId, "items": [_public_item(item) for item in store.list(learnerId, include_archived=includeArchived)]}

    @router.get("/{mistake_id}")
    def get_mistake(mistake_id: str) -> dict[str, Any]:
        item = store.get(mistake_id)
        if not item:
            raise HTTPException(status_code=404, detail="错题不存在")
        return _public_item(item)

    @router.patch("/{mistake_id}")
    def confirm_mistake(mistake_id: str, confirmation: MistakeConfirmation) -> dict[str, Any]:
        item = store.confirm(mistake_id, confirmation.model_dump())
        if not item:
            raise HTTPException(status_code=404, detail="错题不存在")
        log_event(
            "mistake.confirmed",
            mistake_id=mistake_id,
            knowledge_point=item["knowledgePoint"],
            error_reason=item["errorReason"],
        )
        return _public_item(item)

    @router.patch("/{mistake_id}/archive")
    def archive_mistake(mistake_id: str, request: MistakeArchiveRequest) -> dict[str, Any]:
        item = store.set_archived(mistake_id, request.archived)
        if not item:
            raise HTTPException(status_code=404, detail="错题不存在")
        cleared_threads = 0
        if request.archived and archive_cleanup:
            cleared_threads = int(archive_cleanup(item["mistakeId"], item["learnerId"]) or 0)
        log_event(
            "mistake.archived", mistake_id=mistake_id, archived=request.archived,
            cleared_tutor_threads=cleared_threads,
        )
        return _public_item(item)

    @router.get("/{mistake_id}/source", response_class=FileResponse)
    def get_mistake_source(mistake_id: str) -> FileResponse:
        path = store.source_path(mistake_id)
        if not path or not path.is_file():
            raise HTTPException(status_code=404, detail="错题原图不存在")
        item = store.get(mistake_id)
        return FileResponse(path, media_type=item["contentType"], filename=item["sourceFilename"])

    @router.get("/{mistake_id}/assets/{filename}", response_class=FileResponse)
    def get_mistake_asset(mistake_id: str, filename: str) -> FileResponse:
        safe_name = Path(filename).name
        if safe_name != filename:
            raise HTTPException(status_code=400, detail="资源名称无效")
        item = store.get(mistake_id)
        if not item:
            raise HTTPException(status_code=404, detail="错题不存在")
        path = store.item_directory(mistake_id) / "assets" / safe_name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="错题资源不存在")
        return FileResponse(path)

    return router


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "sourceImagePath"}
