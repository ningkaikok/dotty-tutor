"""Teacher studio and learner delivery routes for lesson videos."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from application.services.quality_review import quality_review_actor
from domain.contracts.lesson_media import (
    LessonMediaDraftCreate,
    LessonMediaListResponse,
    LessonMediaPublishedResponse,
    LessonMediaPublishRequest,
    LessonMediaRenderRequest,
    LessonMediaResponse,
    LessonMediaReviewRequest,
    LessonMediaRevisionCreate,
)


def build_lesson_media_router(service: Any) -> APIRouter:
    """Build the isolated teacher API and published-only learner projection."""
    router = APIRouter(tags=["lesson-media"])
    teacher = APIRouter(prefix="/api/lesson-media")
    learner = APIRouter(prefix="/api/chapters")

    def call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except PermissionError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    def actor(request: Request, display_name: str | None = None) -> dict[str, Any]:
        return call(quality_review_actor, request, display_name=display_name or "")

    @teacher.post("/drafts", response_model=LessonMediaResponse, status_code=201)
    def create_draft(request: Request, payload: LessonMediaDraftCreate) -> dict[str, Any]:
        reviewer = actor(request)
        return call(service.create_draft, payload.model_dump(), created_by=reviewer["actorId"])

    @teacher.get("/lessons/{chapter_id}/{publication_id}/{lesson_id}", response_model=LessonMediaListResponse)
    def list_for_lesson(request: Request, chapter_id: str, publication_id: str, lesson_id: str) -> dict[str, Any]:
        actor(request)
        return call(service.list_for_lesson, chapter_id, publication_id, lesson_id)

    @teacher.get("/{media_id}", response_model=LessonMediaResponse)
    def get_revision(request: Request, media_id: str) -> dict[str, Any]:
        actor(request)
        return call(service.get, media_id)

    @teacher.get("/{media_id}/asset", response_class=FileResponse)
    def teacher_asset(request: Request, media_id: str) -> FileResponse:
        actor(request)
        path = call(service.teacher_asset, media_id, kind="asset")
        return FileResponse(path, media_type="video/mp4", filename=f"{media_id}.mp4", content_disposition_type="inline", headers={"Cache-Control": "private, no-store"})

    @teacher.get("/{media_id}/captions", response_class=FileResponse)
    def teacher_captions(request: Request, media_id: str) -> FileResponse:
        actor(request)
        path = call(service.teacher_asset, media_id, kind="captions")
        return FileResponse(path, media_type="text/vtt; charset=utf-8", filename=f"{media_id}.vtt", content_disposition_type="inline", headers={"Cache-Control": "private, no-store"})

    @teacher.get("/{media_id}/captions/download", response_class=FileResponse)
    def teacher_captions_download(request: Request, media_id: str) -> FileResponse:
        actor(request)
        path = call(service.teacher_asset, media_id, kind="captions")
        return FileResponse(path, media_type="text/vtt; charset=utf-8", filename=f"{media_id}.vtt", headers={"Cache-Control": "private, no-store"})

    @teacher.post("/{media_id}/revisions", response_model=LessonMediaResponse, status_code=201)
    def create_revision(request: Request, media_id: str, payload: LessonMediaRevisionCreate) -> dict[str, Any]:
        reviewer = actor(request)
        return call(service.create_revision, media_id, payload.model_dump(), created_by=reviewer["actorId"])

    @teacher.post("/{media_id}/render", status_code=202)
    def enqueue_render(request: Request, media_id: str, payload: LessonMediaRenderRequest) -> dict[str, Any]:
        actor(request)
        return call(service.enqueue_render, media_id, expected_version=payload.expectedVersion)

    @teacher.delete("/{media_id}/render")
    def cancel_render(request: Request, media_id: str) -> dict[str, Any]:
        actor(request)
        return call(service.cancel_render, media_id)

    @teacher.post("/{media_id}/review", response_model=LessonMediaResponse)
    def review(request: Request, media_id: str, payload: LessonMediaReviewRequest) -> dict[str, Any]:
        reviewer = actor(request, payload.displayName)
        return call(service.review, media_id, payload.model_dump(), actor=reviewer)

    @teacher.post("/{media_id}/publish", response_model=LessonMediaResponse)
    def publish(request: Request, media_id: str, payload: LessonMediaPublishRequest) -> dict[str, Any]:
        reviewer = actor(request, payload.displayName)
        return call(service.publish, media_id, actor=reviewer)

    @learner.get("/{chapter_id}/media/published", response_model=LessonMediaPublishedResponse)
    def published_for_student(chapter_id: str, publicationId: str, lessonId: str) -> dict[str, Any]:
        return call(service.published_for_student, chapter_id, publicationId, lessonId)

    @learner.get("/{chapter_id}/media/published/{media_id}/asset", response_class=FileResponse)
    def published_asset(chapter_id: str, media_id: str) -> FileResponse:
        path = call(service.public_asset, chapter_id, media_id, kind="asset")
        return FileResponse(
            path,
            media_type="video/mp4",
            filename=f"{media_id}.mp4",
            content_disposition_type="inline",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @learner.get("/{chapter_id}/media/published/{media_id}/captions", response_class=FileResponse)
    def published_captions(chapter_id: str, media_id: str) -> FileResponse:
        path = call(service.public_asset, chapter_id, media_id, kind="captions")
        return FileResponse(
            path,
            media_type="text/vtt; charset=utf-8",
            filename=f"{media_id}.vtt",
            content_disposition_type="inline",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @learner.get("/{chapter_id}/media/published/{media_id}/captions/download", response_class=FileResponse)
    def published_captions_download(chapter_id: str, media_id: str) -> FileResponse:
        path = call(service.public_asset, chapter_id, media_id, kind="captions")
        return FileResponse(
            path,
            media_type="text/vtt; charset=utf-8",
            filename=f"{media_id}.vtt",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    router.include_router(teacher)
    router.include_router(learner)
    return router


__all__ = ["build_lesson_media_router"]
