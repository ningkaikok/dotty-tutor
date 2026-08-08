"""HTTP routes for browsing and soft-deleting processed textbooks."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException

from observability import log_event


def build_library_router(*, store: Any, upload_registry: Any, lesson_store: dict[str, Any]) -> APIRouter:
    """Build the library adapter without importing the ASGI composition root."""
    router = APIRouter(tags=["textbook-library"])

    @router.get("/api/library")
    def list_textbook_library() -> dict[str, Any]:
        """List non-deleted completed imports from PostgreSQL."""
        return {"items": store.list_imports()}

    @router.delete("/api/library/{upload_id}")
    def delete_textbook_library_item(upload_id: str) -> dict[str, str]:
        """Soft-delete library metadata while preserving recoverable source data."""
        job = upload_registry.get(upload_id)
        if not store.soft_delete_import(upload_id):
            raise HTTPException(status_code=404, detail="教材不存在或已删除")
        # Evict process memory only after the durable row changed. Files and
        # database rows remain available for a future restore operation.
        for payload in job.get("batchPayloads", {}).values():
            lesson_store.pop(payload["question"]["id"], None)
        upload_registry.uploads.pop(upload_id, None)
        log_event("library.item.deleted", upload_id=upload_id, filename=job.get("filename"))
        return {"status": "deleted", "uploadId": upload_id}

    @router.get("/api/library/{upload_id}")
    def get_textbook_library_item(upload_id: str) -> dict[str, Any]:
        """Restore persisted questions in their original numerical order."""
        job = upload_registry.get(upload_id)
        result = job.get("result")
        if job.get("status") == "deleted":
            raise HTTPException(status_code=404, detail="教材已删除")
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

    return router
