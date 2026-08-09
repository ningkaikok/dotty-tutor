"""HTTP boundary for publishing interactive lesson collections."""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from lesson_contracts import PublicationCreate, PublicationStatusUpdate
from observability import log_event


def _public_lesson(lesson: dict[str, Any]) -> dict[str, Any]:
    """Remove studio-only diagnostics before a lesson crosses into /learn."""
    payload = dict(lesson.get("questionPayload") or {})
    question = dict(payload.get("question") or {})
    for key in ("publicationStatus", "sourceArtifactUrl", "promptArtifactUrl"):
        question.pop(key, None)
    payload["question"] = question
    payload.pop("review", None)
    payload.pop("quality", None)
    payload["architecture"] = {}
    payload["modelRun"] = {
        "requestedProvider": "published",
        "provider": "published",
        "model": "published",
        "fallback": False,
    }
    return {
        "lessonId": lesson["lessonId"],
        "title": lesson["title"],
        "version": lesson["version"],
        "status": lesson["status"],
        "knowledgePoints": lesson["knowledgePoints"],
        "blocks": lesson["blocks"],
        "questionPayload": payload,
        "guideCards": [],
    }


def build_publication_router(*, store: Any) -> APIRouter:
    router = APIRouter(prefix="/api/publications", tags=["publications"])

    @router.post("")
    def create_publication(request: PublicationCreate) -> dict[str, Any]:
        try:
            publication = store.create_publication(
                publication_id=uuid.uuid4().hex,
                title=request.title,
                source_upload_id=request.sourceUploadId,
                lesson_ids=request.lessonIds,
                status="draft",
                created_at=time.time(),
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        log_event(
            "publication.created",
            publication_id=publication["publicationId"],
            lesson_count=len(request.lessonIds),
            status="draft",
        )
        return publication

    @router.get("")
    def list_publications(status: str = "published") -> dict[str, Any]:
        if status not in {"draft", "in_review", "review", "published", "archived"}:
            raise HTTPException(status_code=400, detail="无效的发布状态")
        return {"items": store.list_publications(status=status)}

    @router.get("/{publication_id}")
    def get_publication(publication_id: str) -> dict[str, Any]:
        publication = store.load_publication(publication_id)
        if not publication:
            raise HTTPException(status_code=404, detail="互动试卷不存在")
        if publication["status"] != "published":
            raise HTTPException(status_code=404, detail="互动试卷尚未发布")
        publication["lessons"] = [_public_lesson(lesson) for lesson in publication["lessons"]]
        return publication

    @router.patch("/{publication_id}/status")
    def update_publication_status(
        publication_id: str,
        request: PublicationStatusUpdate,
    ) -> dict[str, Any]:
        try:
            publication = store.update_publication_status(publication_id, request.status)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if not publication:
            raise HTTPException(status_code=404, detail="互动试卷不存在")
        log_event(
            "publication.status.updated",
            publication_id=publication_id,
            status=request.status,
        )
        return publication

    return router
