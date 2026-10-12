"""Thin courseware adapters to the existing media review and voice turn APIs."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from auth_context import learner_for_request
from domain.contracts.courseware import (
    CoursewareMediaCreate,
    CoursewareVoiceSessionCreate,
)
from domain.contracts.lesson_media import (
    LessonMediaListResponse,
    LessonMediaPublishedResponse,
    LessonMediaResponse,
)


def build_courseware_delivery_router(media: Any, voice: Any, call: Callable[..., Any], teacher_actor: Callable[..., str]) -> APIRouter:
    router = APIRouter()

    @router.post("/courseware/{courseware_id}/media", response_model=LessonMediaResponse, status_code=201)
    def create_media(courseware_id: str, payload: CoursewareMediaCreate, request: Request) -> dict[str, Any]:
        actor = teacher_actor(request)
        return call(media.create_draft, {**payload.model_dump(), "coursewareId": courseware_id, "sourceKind": "courseware"}, created_by=actor)

    @router.get("/courseware/{courseware_id}/media", response_model=LessonMediaListResponse)
    def list_media(courseware_id: str, publicationId: str, request: Request) -> dict[str, Any]:
        teacher_actor(request)
        return call(media.list_for_courseware, courseware_id, publicationId)

    @router.get("/courseware/{courseware_id}/media/published", response_model=LessonMediaPublishedResponse)
    def published_media(courseware_id: str, publicationId: str) -> dict[str, Any]:
        return call(media.list_for_courseware, courseware_id, publicationId, public=True)

    @router.get("/courseware/{courseware_id}/media/published/{media_id}/asset", response_class=FileResponse)
    def asset(courseware_id: str, media_id: str, publicationId: str) -> FileResponse:
        path = call(media.courseware_asset, courseware_id, publicationId, media_id, kind="asset")
        return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, no-store"})

    @router.get("/courseware/{courseware_id}/media/published/{media_id}/captions", response_class=FileResponse)
    def captions(courseware_id: str, media_id: str, publicationId: str) -> FileResponse:
        path = call(media.courseware_asset, courseware_id, publicationId, media_id, kind="captions")
        return FileResponse(path, media_type="text/vtt", headers={"Cache-Control": "private, no-store"})

    @router.get("/courseware/{courseware_id}/media/published/{media_id}/captions/download", response_class=FileResponse)
    def download(courseware_id: str, media_id: str, publicationId: str) -> FileResponse:
        path = call(media.courseware_asset, courseware_id, publicationId, media_id, kind="captions")
        return FileResponse(path, media_type="text/vtt", filename=f"{media_id}.vtt", headers={"Cache-Control": "private, no-store"})

    @router.post("/courseware/{courseware_id}/voice-sessions", status_code=201)
    def create_voice(courseware_id: str, payload: CoursewareVoiceSessionCreate, request: Request) -> dict[str, Any]:
        values = payload.model_dump()
        values["learnerId"] = learner_for_request(request, payload.learnerId)
        return call(voice.create_courseware_session, courseware_id, values)

    return router
