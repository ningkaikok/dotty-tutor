"""Tutor 检索 HTTP 边界。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


class TutorSearchIndexRequest(BaseModel):
    publicationId: str = Field(min_length=1, max_length=64)


def build_tutor_search_router(*, store: Any, search_service: Any, search_store: Any) -> APIRouter:
    router = APIRouter(prefix="/api/tutor/search", tags=["tutor-search"])

    @router.get("")
    def search(q: str = "", publicationId: str | None = None, limit: int = 5) -> dict[str, Any]:
        return {"items": search_store.search(query=q, publication_id=publicationId, limit=limit)}

    @router.post("/rebuild")
    def rebuild(request: TutorSearchIndexRequest) -> dict[str, Any]:
        publication = store.load_publication(request.publicationId)
        if not publication:
            raise HTTPException(status_code=404, detail="互动试卷不存在")
        try:
            count = search_service.rebuild_publication(publication)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"publicationId": request.publicationId, "indexed": count}

    return router
