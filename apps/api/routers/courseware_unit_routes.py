"""Identity-aware navigation and reading positions for chapter-backed units."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from application.errors import AppError
from application.services.courseware_unit import CoursewareUnitService
from auth_context import learner_for_request
from domain.contracts.courseware_unit import (
    CoursewarePositionSave,
    CoursewareReadingPosition,
    CoursewareUnit,
    CoursewareUnitsResponse,
)


def build_courseware_unit_router(service: CoursewareUnitService) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["courseware-unit"])

    def call(fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except HTTPException as error:
            raise AppError(str(error.detail), status_code=error.status_code, error_code="COURSEWARE_UNIT_ACCESS_DENIED") from error
        except LookupError as error:
            raise AppError(str(error), status_code=404, error_code="COURSEWARE_UNIT_NOT_FOUND") from error
        except PermissionError as error:
            raise AppError(str(error), status_code=403, error_code="COURSEWARE_UNIT_FORBIDDEN") from error
        except ValueError as error:
            raise AppError(str(error), status_code=409, error_code="COURSEWARE_POSITION_CONFLICT") from error

    def teacher(request: Request) -> None:
        if getattr(request.app.state, "auth_mode", "demo") == "protected":
            actor = getattr(request.state, "actor", None)
            if not actor or actor.get("role") != "teacher":
                raise AppError("此操作需要教师会话", status_code=403, error_code="TEACHER_SESSION_REQUIRED")

    @router.get("/courseware-units", response_model=CoursewareUnitsResponse)
    def list_units(request: Request, learnerId: str | None = None) -> dict[str, Any]:
        return call(service.list_units, learner_id=call(learner_for_request, request, learnerId))

    @router.get("/courseware/{courseware_id}/unit", response_model=CoursewareUnit)
    def student_unit(courseware_id: str, request: Request, learnerId: str | None = None) -> dict[str, Any]:
        return call(service.directory_for_courseware, courseware_id,
                    learner_id=call(learner_for_request, request, learnerId))

    @router.get("/chapters/{chapter_id}/courseware-unit", response_model=CoursewareUnit)
    def teacher_unit(chapter_id: str, request: Request) -> dict[str, Any]:
        teacher(request)
        return call(service.directory, chapter_id, teacher=True)

    @router.get("/courseware/{courseware_id}/teaching-unit", response_model=CoursewareUnit)
    def teaching_unit(courseware_id: str, request: Request) -> dict[str, Any]:
        teacher(request)
        return call(service.directory_for_courseware, courseware_id, learner_id="", teacher=True)

    @router.put("/courseware/{courseware_id}/progress", response_model=CoursewareReadingPosition)
    def save_position(courseware_id: str, payload: CoursewarePositionSave, request: Request,
                      learnerId: str | None = None) -> dict[str, Any]:
        return call(service.save_position, courseware_id, request=payload,
                    learner_id=call(learner_for_request, request, learnerId))

    return router
