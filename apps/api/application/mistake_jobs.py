"""Background handlers for durable mistake-image imports."""

from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from application.job_worker import (
    CancellableJobResult,
    JobCancelled,
    RetryableJobError,
    TaskRegistry,
)

CancellationCheck = Callable[[], bool]


def mistake_id_for_capture(capture_id: str, learner_id: str | None = None) -> str:
    """Derive a stable, learner-scoped item id for one capture."""
    identity = f"{learner_id}:{capture_id}" if learner_id else capture_id
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return f"capture-{digest}"


def cleanup_queued_mistake_capture(job: dict[str, Any], *, data_root: Path) -> bool:
    """Remove only a queued mistake capture that was cancelled before claim.

    The path and deterministic learner-scoped id are checked together so a
    malformed job cannot turn cancellation into arbitrary file deletion.
    """
    if job.get("jobType") != "mistake.image.import":
        return False
    payload = job.get("payload") or {}
    capture_id = str(payload.get("captureId") or "")
    learner_id = str(payload.get("learnerId") or "")
    mistake_id = str(payload.get("mistakeId") or "")
    job_id = str(job.get("jobId") or payload.get("jobId") or "")
    allowed_ids = {
        mistake_id_for_capture(capture_id, learner_id),
        # Preserve cleanup for jobs queued before learner scoping was added.
        mistake_id_for_capture(capture_id),
    }
    if not capture_id or not learner_id or mistake_id not in allowed_ids:
        return False
    root = (Path(data_root).expanduser().resolve() / "mistakes").resolve()
    directory = (root / mistake_id).resolve()
    source_path = Path(str(payload.get("sourcePath") or "")).expanduser().resolve()
    allowed_directories = {directory}
    if job_id:
        allowed_directories.add(directory / job_id)
    if directory.parent != root or source_path.parent not in allowed_directories:
        return False
    target = source_path.parent if job_id else directory
    if not target.is_dir():
        return False
    shutil.rmtree(target, ignore_errors=True)
    return True


def build_mistake_registry(
    *,
    store: Any,
    recognize: Any,
    project_result: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> TaskRegistry:
    """Register the image import handler on the existing generic Worker."""
    registry = TaskRegistry()

    @registry.decorator("mistake.image.import")
    def import_mistake(
        payload: dict[str, Any], cancellation_check: CancellationCheck
    ) -> dict[str, Any] | CancellableJobResult:
        capture_id = str(payload["captureId"])
        learner_id = str(payload.get("learnerId") or "local-demo")
        mistake_id = str(payload.get("mistakeId") or mistake_id_for_capture(capture_id, learner_id))
        source_image_path = str(payload["sourcePath"])
        source_path = Path(source_image_path).expanduser().resolve()
        expected_root = store.mistake_root.resolve()
        job_id = str(payload.get("jobId") or "")
        legacy_directory = expected_root / mistake_id_for_capture(capture_id)
        allowed_directories = {legacy_directory}
        if job_id:
            allowed_directories.add(expected_root / mistake_id / job_id)
        else:
            allowed_directories.add(expected_root / mistake_id)
        if source_path.parent not in allowed_directories:
            raise RetryableJobError("错题图片路径不在数据目录内")

        asset_directory = source_path.parent / "assets"
        asset_prefix = f"/api/mistakes/{mistake_id}/assets"

        def cleanup_capture() -> None:
            # Only delete a row whose source path still belongs to this capture;
            # a later successful retry or another learner's record is never a
            # valid cancellation cleanup target.
            delete_capture = getattr(store, "delete_capture", None)
            try:
                if delete_capture is not None:
                    delete_capture(mistake_id, source_image_path=source_image_path)
            finally:
                shutil.rmtree(source_path.parent, ignore_errors=True)

        def cleanup_source_directory() -> None:
            """Remove this execution's files without touching a winner row."""
            shutil.rmtree(source_path.parent, ignore_errors=True)

        existing = store.get(mistake_id)
        if existing:
            projected = project_result(existing) if project_result else existing
            existing_source = str(existing.get("sourceImagePath") or "")
            owns_existing_capture = (
                Path(existing_source).expanduser().resolve() == source_path
                if existing_source
                else False
            )
            return CancellableJobResult(
                projected,
                on_cancel=cleanup_capture if owns_existing_capture else cleanup_source_directory,
            )

        def check(stage: str) -> None:
            if cancellation_check():
                raise JobCancelled(f"已请求取消：{stage}")

        try:
            check("file_saved")
            result = recognize(
                source_path,
                str(payload.get("sourceText") or ""),
                asset_directory,
                asset_prefix,
                stage_check=check,
            )
            check("ocr_and_model_complete")
        except JobCancelled:
            cleanup_capture()
            raise
        except Exception as error:
            if cancellation_check():
                cleanup_capture()
                raise JobCancelled("已请求取消：provider 返回") from error
            raise RetryableJobError(f"错题识别失败：{error}") from error

        payload_data, guide_cards, ocr_run, model_run = result
        check("final_write_before")
        question = payload_data.get("question", {})
        now = time.time()
        item_data = {
            "mistakeId": mistake_id,
            "learnerId": learner_id,
            "sourceFilename": str(payload.get("filename") or source_path.name),
            "contentType": str(payload.get("contentType") or "application/octet-stream"),
            "sourceImagePath": source_image_path,
            "sourceImageUrl": f"/api/mistakes/{mistake_id}/source",
            "questionPayload": payload_data,
            "guideCards": guide_cards,
            "ocrRun": ocr_run,
            "modelRun": model_run,
            "originalAnswer": str(payload.get("originalAnswer") or ""),
            "subject": "数学",
            "gradeBand": "初中",
            "chapter": str(question.get("chapter") or "待确认"),
            "knowledgePoint": str(question.get("knowledgePoint") or "待确认"),
            "status": "pending_confirmation",
            "createdAt": now,
            "updatedAt": now,
        }
        create_capture_with_status = getattr(store, "create_capture_with_status", None)
        if create_capture_with_status is not None:
            item, created_by_this_run = create_capture_with_status(item_data)
        else:
            create_capture = getattr(store, "create_capture", None)
            item = create_capture(item_data) if create_capture is not None else store.create(item_data)
            # Older test doubles/stores predate the explicit winner signal. The
            # production store implements create_capture_with_status, while
            # this conservative fallback still preserves the old API.
            created_by_this_run = True
        item_source = str(item.get("sourceImagePath") or "")
        owns_capture = created_by_this_run or (
            bool(item_source) and Path(item_source).expanduser().resolve() == source_path
        )
        try:
            check("final_write_after")
        except JobCancelled:
            if owns_capture:
                cleanup_capture()
            else:
                cleanup_source_directory()
            raise
        projected = project_result(item) if project_result else item
        return CancellableJobResult(
            projected,
            on_cancel=cleanup_capture if owns_capture else cleanup_source_directory,
        )

    return registry


__all__ = ["build_mistake_registry", "cleanup_queued_mistake_capture", "mistake_id_for_capture"]
