"""HTTP adapter for textbook courseware authoring and learner playback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from application.errors import AppError
from auth_context import learner_for_request
from domain.contracts.courseware import (
    CoursewareAttemptRequest,
    CoursewareAttemptResponse,
    CoursewareCoverage,
    CoursewareDocument,
    CoursewareDocumentsResponse,
    CoursewareJobSummary,
    CoursewarePageEdit,
    CoursewarePlan,
    CoursewarePlanApprovalResult,
    CoursewarePlanApprove,
    CoursewarePlanCreate,
    CoursewarePlanEdit,
    CoursewarePlanResult,
    CoursewarePresentation,
    CoursewareProfilesResponse,
    CoursewarePublication,
    CoursewarePublicDocument,
    CoursewarePublishRequest,
    CoursewareRegenerateRequest,
    CoursewareReviewRequest,
)


class CoursewareRegenerationResult(BaseModel):
    """Stable page-regeneration response composed from existing domain contracts."""

    document: CoursewareDocument
    job: CoursewareJobSummary


def build_courseware_router(service: Any, *, media_service: Any | None = None, voice_service: Any | None = None) -> APIRouter:
    """Build the typed API boundary around the courseware application service."""
    router = APIRouter(prefix="/api", tags=["courseware"])

    def call(fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except AppError:
            raise
        except HTTPException as error:
            detail = error.detail if isinstance(error.detail, str) else "请求未获授权"
            raise AppError(
                detail,
                status_code=error.status_code,
                error_code="COURSEWARE_ACCESS_DENIED" if error.status_code in {401, 403} else "COURSEWARE_REQUEST_REJECTED",
                headers=error.headers,
            ) from error
        except IntegrityError as error:
            raise AppError(
                "课件已被其他操作更新，请刷新后重试",
                status_code=409,
                error_code="COURSEWARE_CONFLICT",
            ) from error
        except LookupError as error:
            raise AppError(str(error), status_code=404, error_code="COURSEWARE_NOT_FOUND") from error
        except PermissionError as error:
            raise AppError(str(error), status_code=403, error_code="COURSEWARE_FORBIDDEN") from error
        except FileNotFoundError as error:
            raise AppError("课件素材不存在", status_code=404, error_code="COURSEWARE_ASSET_NOT_FOUND") from error
        except (ValueError, RuntimeError) as error:
            raise AppError(str(error), status_code=409, error_code="COURSEWARE_STATE_CONFLICT") from error

    def teacher_actor_id(request: Request) -> str:
        actor = getattr(request.state, "actor", None)
        auth_mode = getattr(request.app.state, "auth_mode", "demo")
        if auth_mode == "protected":
            if not actor or actor.get("role") != "teacher":
                raise AppError(
                    "此操作需要教师会话",
                    status_code=403,
                    error_code="TEACHER_SESSION_REQUIRED",
                )
            actor_id = actor.get("sessionId")
            if not actor_id:
                raise AppError("教师会话身份无效", status_code=401, error_code="INVALID_ACTOR")
            return str(actor_id)
        if actor and actor.get("role") == "teacher" and actor.get("sessionId"):
            return str(actor["sessionId"])
        return "demo-teacher"

    @router.get("/chapters/{chapter_id}/courseware-coverage", response_model=CoursewareCoverage)
    def coverage(chapter_id: str, request: Request) -> dict[str, Any]:
        teacher_actor_id(request)
        return call(service.get_coverage, chapter_id)

    @router.get("/courseware/profiles", response_model=CoursewareProfilesResponse)
    def list_profiles() -> dict[str, Any]:
        return call(service.list_profiles)

    @router.post(
        "/chapters/{chapter_id}/courseware-plan",
        response_model=CoursewarePlanResult,
        status_code=202,
    )
    def create_plan(
        chapter_id: str,
        payload: CoursewarePlanCreate,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        teacher_actor_id(request)
        return call(service.create_plan, chapter_id, payload, idempotency_key=idempotency_key)

    @router.get("/chapters/{chapter_id}/courseware-plan", response_model=CoursewarePlan)
    def get_plan(chapter_id: str, request: Request) -> dict[str, Any]:
        teacher_actor_id(request)
        plan = call(service.get_plan, chapter_id)
        if plan is None:
            raise AppError("尚未创建课件规划", status_code=404, error_code="COURSEWARE_PLAN_NOT_FOUND")
        return plan

    @router.put("/chapters/{chapter_id}/courseware-plan", response_model=CoursewarePlan)
    def edit_plan(chapter_id: str, payload: CoursewarePlanEdit, request: Request) -> dict[str, Any]:
        teacher_actor_id(request)
        return call(service.edit_plan, chapter_id, payload)

    @router.post(
        "/chapters/{chapter_id}/courseware-plan/approve",
        response_model=CoursewarePlanApprovalResult,
        status_code=202,
    )
    def approve_plan(chapter_id: str, payload: CoursewarePlanApprove, request: Request) -> dict[str, Any]:
        teacher_actor_id(request)
        return call(
            service.approve_plan,
            chapter_id,
            plan_version=payload.planVersion,
            expected_record_version=payload.expectedRecordVersion,
            command_id=payload.commandId,
        )

    @router.get("/chapters/{chapter_id}/courseware", response_model=CoursewareDocumentsResponse)
    def list_chapter_documents(chapter_id: str, request: Request) -> dict[str, Any]:
        teacher_actor_id(request)
        return call(service.list_chapter_documents, chapter_id)

    @router.get("/courseware/{courseware_id}", response_model=CoursewareDocument)
    def get_document(courseware_id: str, request: Request) -> dict[str, Any]:
        teacher_actor_id(request)
        return call(service.get_document, courseware_id)

    @router.put("/courseware/{courseware_id}/pages/{page_id}", response_model=CoursewareDocument)
    def edit_page(
        courseware_id: str,
        page_id: str,
        payload: CoursewarePageEdit,
        request: Request,
    ) -> dict[str, Any]:
        teacher_actor_id(request)
        return call(
            service.edit_page,
            courseware_id,
            page_id,
            content=payload.page,
            expected_record_version=payload.expectedRecordVersion,
        )

    @router.post(
        "/courseware/{courseware_id}/pages/{page_id}/regenerate",
        response_model=CoursewareRegenerationResult,
        status_code=202,
    )
    def regenerate_page(
        courseware_id: str,
        page_id: str,
        payload: CoursewareRegenerateRequest,
        request: Request,
    ) -> dict[str, Any]:
        teacher_actor_id(request)
        return call(
            service.regenerate_page,
            courseware_id,
            page_id,
            expected_record_version=payload.expectedRecordVersion,
            reason=payload.reason,
            command_id=payload.commandId,
        )

    @router.post("/courseware/{courseware_id}/review", response_model=CoursewareDocument)
    def review_document(
        courseware_id: str,
        payload: CoursewareReviewRequest,
        request: Request,
    ) -> dict[str, Any]:
        actor_id = teacher_actor_id(request)
        return call(
            service.review_document,
            courseware_id,
            decision=payload.decision,
            page_ids=payload.pageIds,
            note=payload.note,
            expected_record_version=payload.expectedRecordVersion,
            actor_id=actor_id,
        )

    @router.post("/courseware/{courseware_id}/publish", response_model=CoursewarePublication)
    def publish(
        courseware_id: str,
        payload: CoursewarePublishRequest,
        request: Request,
    ) -> dict[str, Any]:
        teacher_actor_id(request)
        return call(
            service.publish,
            courseware_id,
            expected_record_version=payload.expectedRecordVersion,
        )

    @router.get("/courseware/{courseware_id}/published", response_model=CoursewarePublicDocument)
    def get_published(
        courseware_id: str,
        publicationId: str | None = Query(default=None),
    ) -> dict[str, Any]:
        # This endpoint deliberately exposes only the publication projection. It
        # never falls back to a teacher document when a publication is unavailable.
        return call(service.get_published, courseware_id, publicationId)

    @router.get("/courseware/{courseware_id}/presentation", response_model=CoursewarePresentation)
    def get_presentation(
        courseware_id: str,
        request: Request,
        publicationId: str | None = Query(default=None),
    ) -> dict[str, Any]:
        teacher_actor_id(request)
        return call(service.get_presentation, courseware_id, publicationId)

    @router.get("/courseware/{courseware_id}/assets/{asset_id}", response_class=FileResponse)
    def get_asset(
        courseware_id: str,
        asset_id: str,
        request: Request,
        publicationId: str | None = Query(default=None),
    ) -> FileResponse:
        actor = getattr(request.state, "actor", None)
        auth_mode = getattr(request.app.state, "auth_mode", "demo")
        role = actor.get("role") if isinstance(actor, dict) else None
        # Demo mode intentionally treats anonymous requests as the local teacher.
        # Protected students never inherit preview access from a publication URL.
        teacher_preview = role != "student" and (
            auth_mode == "demo" or (auth_mode == "protected" and role == "teacher")
        )
        if teacher_preview:
            teacher_actor_id(request)
        path, media_type = call(
            service.get_chapter_assets,
            courseware_id,
            asset_id,
            publication_id=publicationId,
            teacher_preview=teacher_preview,
        )
        return FileResponse(
            Path(path),
            media_type=media_type,
            headers={"Cache-Control": "private, no-store"},
        )

    @router.post("/courseware/{courseware_id}/attempts", response_model=CoursewareAttemptResponse)
    def record_attempt(
        courseware_id: str,
        payload: CoursewareAttemptRequest,
        request: Request,
    ) -> dict[str, Any]:
        learner_id = call(learner_for_request, request, None)
        return call(service.attempt, courseware_id, learner_id=learner_id, request=payload)

    @router.delete("/courseware/{courseware_id}")
    def delete_draft(
        courseware_id: str,
        request: Request,
        expectedRecordVersion: int = Query(ge=1),
    ) -> dict[str, Any]:
        teacher_actor_id(request)
        return call(
            service.delete_draft,
            courseware_id,
            expected_record_version=expectedRecordVersion,
        )

    if media_service is not None and voice_service is not None:
        from routers.courseware_delivery_routes import build_courseware_delivery_router

        router.include_router(build_courseware_delivery_router(media_service, voice_service, call, teacher_actor_id))
    return router


__all__ = ["build_courseware_router"]
