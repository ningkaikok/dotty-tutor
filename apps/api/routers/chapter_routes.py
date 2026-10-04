"""Teacher chapter studio and published learner chapter endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from application.services.chapter_source_preview import ChapterSourcePreview
from auth_context import learner_for_request
from domain.contracts.audit import BackgroundJobSummary
from domain.contracts.chapter import (
    ChapterAIGenerationRequest,
    ChapterAttempt,
    ChapterAttemptResponse,
    ChapterAttemptReview,
    ChapterCreate,
    ChapterLessonEdit,
    ChapterListResponse,
    ChapterPublishedResponse,
    ChapterPublishResponse,
    ChapterResponse,
    ChapterReview,
    ChapterRevisionCreate,
)


def build_chapter_router(service: Any) -> APIRouter:
    router = APIRouter(prefix="/api/chapters", tags=["chapters"])

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post("", response_model=ChapterResponse, status_code=201)
    def create_chapter(request: ChapterCreate) -> dict[str, Any]:
        return call(service.create, request.model_dump())

    @router.get("", response_model=ChapterListResponse)
    def list_chapters() -> dict[str, Any]:
        return service.list()

    @router.post("/from-upload/{upload_id}", response_model=BackgroundJobSummary, status_code=202)
    def create_courses_from_upload(upload_id: str) -> dict[str, Any]:
        from application.services.material_courses import MaterialCourseService
        from infrastructure.runtime.ocr_runtime import runtime
        return call(MaterialCourseService(service, runtime).enqueue, upload_id)

    @router.get("/{chapter_id}", response_model=ChapterResponse)
    def get_chapter(chapter_id: str) -> dict[str, Any]:
        return call(service.get, chapter_id)

    @router.post("/{chapter_id}/revisions", response_model=ChapterResponse)
    def revise_chapter(chapter_id: str, request: ChapterRevisionCreate) -> dict[str, Any]:
        return call(service.revise, chapter_id, request.model_dump())

    @router.post("/{chapter_id}/generate", response_model=ChapterResponse)
    def generate_chapter(chapter_id: str) -> dict[str, Any]:
        return call(service.generate, chapter_id)

    @router.post("/{chapter_id}/generate-ai", response_model=BackgroundJobSummary, status_code=202)
    def generate_ai_chapter(chapter_id: str, request: ChapterAIGenerationRequest) -> dict[str, Any]:
        return call(service.enqueue_ai_generation, chapter_id, expected_record_version=request.expectedRecordVersion)

    @router.get("/{chapter_id}/sources/{revision_id}/pages/{page}/preview", response_class=FileResponse)
    def preview_source_page(chapter_id: str, revision_id: str, page: int) -> FileResponse:
        path = call(ChapterSourcePreview(service.store).get, chapter_id, revision_id, page)
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, no-store"})

    @router.put("/{chapter_id}/lessons/{lesson_id}", response_model=ChapterResponse)
    def edit_lesson(chapter_id: str, lesson_id: str, request: ChapterLessonEdit) -> dict[str, Any]:
        return call(service.edit_lesson, chapter_id, lesson_id, request.model_dump())

    @router.patch("/{chapter_id}/lessons/{lesson_id}/review", response_model=ChapterResponse)
    def review_lesson(chapter_id: str, lesson_id: str, request: ChapterReview) -> dict[str, Any]:
        return call(service.review, chapter_id, lesson_id, request.decision, request.reviewer, request.note, request.expectedRecordVersion)

    @router.post("/{chapter_id}/publish", response_model=ChapterPublishResponse, status_code=201)
    def publish_chapter(chapter_id: str) -> dict[str, Any]:
        return call(service.publish, chapter_id)

    @router.get("/{chapter_id}/published", response_model=ChapterPublishedResponse)
    def get_published_chapter(chapter_id: str, publicationId: str | None = None) -> dict[str, Any]:
        return call(service.get_published, chapter_id, publicationId)

    @router.post("/{chapter_id}/attempts", response_model=ChapterAttemptResponse)
    def record_chapter_attempt(chapter_id: str, http_request: Request, request: ChapterAttempt) -> dict[str, Any]:
        learner_id = learner_for_request(http_request, request.learnerId)
        payload = request.model_dump()
        payload["learnerId"] = learner_id
        return call(service.attempt, chapter_id, payload)

    @router.get("/{chapter_id}/attempts/{attempt_id}", response_model=ChapterAttemptResponse)
    def get_chapter_attempt(chapter_id: str, http_request: Request, attempt_id: str, learnerId: str | None = None) -> dict[str, Any]:
        learner_id = learner_for_request(http_request, learnerId)
        return call(service.get_attempt, chapter_id, attempt_id, learner_id)

    @router.get("/{chapter_id}/review-attempts/{attempt_id}", response_model=ChapterAttemptResponse)
    def get_chapter_attempt_for_review(chapter_id: str, attempt_id: str) -> dict[str, Any]:
        return call(service.get_attempt_for_review, chapter_id, attempt_id)

    @router.patch("/{chapter_id}/attempts/{attempt_id}/review", response_model=ChapterAttemptResponse)
    def review_chapter_attempt(chapter_id: str, attempt_id: str, request: ChapterAttemptReview) -> dict[str, Any]:
        return call(service.review_attempt, chapter_id, attempt_id, request.reviewer, request.decision, request.note)

    return router
