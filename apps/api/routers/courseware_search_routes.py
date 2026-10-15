"""Bounded public textbook retrieval; every operation checks the frozen publication."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse

from domain.contracts.courseware_search import (
    CoursewareSourceExplainRequest,
    CoursewareSourceExplanation,
    CoursewareSourceSearchResponse,
)


def build_courseware_search_router(service: Any) -> APIRouter:
    router = APIRouter(prefix="/api/courseware/{courseware_id}/source-search", tags=["courseware-search"])

    def call(fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get("", response_model=CoursewareSourceSearchResponse)
    def search(courseware_id: str, response: Response, publicationId: str = Query(min_length=1, max_length=64),
               q: str = Query(default="", max_length=500), limit: int = Query(default=5, ge=1, le=10)) -> dict[str, Any]:
        response.headers["Cache-Control"] = "private, no-store"
        return call(service.search, courseware_id, publicationId, q, limit=limit)

    @router.post("/explain", response_model=CoursewareSourceExplanation)
    def explain(courseware_id: str, payload: CoursewareSourceExplainRequest, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "private, no-store"
        return call(service.explain, courseware_id, payload.publicationId, payload.question)

    @router.get("/evidence/{document_id}/preview", response_class=FileResponse)
    def preview(courseware_id: str, document_id: str, publicationId: str = Query(min_length=1, max_length=64)) -> FileResponse:
        path = call(service.preview, courseware_id, publicationId, document_id)
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, no-store"})

    return router
