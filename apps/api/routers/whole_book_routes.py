"""Teacher-only HTTP boundary for whole-book courseware automation."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy.exc import IntegrityError

from application.errors import AppError
from domain.contracts.whole_book import (
    WholeBookControlRequest,
    WholeBookRunView,
    WholeBookStartRequest,
)


def build_whole_book_router(service: Any) -> APIRouter:
    """Expose explicit start and controls plus read-only progress endpoints."""
    router = APIRouter(prefix="/api", tags=["whole-book courseware"])

    def actor_id(request: Request) -> str:
        actor = getattr(request.state, "actor", None)
        auth_mode = getattr(request.app.state, "auth_mode", "demo")
        if auth_mode == "protected":
            if not actor or actor.get("role") != "teacher":
                raise AppError("此操作需要教师会话", status_code=403, error_code="TEACHER_SESSION_REQUIRED")
            value = actor.get("sessionId")
            if not value:
                raise AppError("教师会话身份无效", status_code=401, error_code="INVALID_ACTOR")
            return str(value)
        if actor and actor.get("role") == "teacher" and actor.get("sessionId"):
            return str(actor["sessionId"])
        return "demo-teacher"

    def call(fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except AppError:
            raise
        except LookupError as error:
            raise AppError(str(error), status_code=404, error_code="WHOLE_BOOK_NOT_FOUND") from error
        except PermissionError as error:
            raise AppError(str(error), status_code=403, error_code="WHOLE_BOOK_FORBIDDEN") from error
        except IntegrityError as error:
            raise AppError("整本任务已被其他操作更新，请刷新后重试", status_code=409,
                           error_code="WHOLE_BOOK_CONFLICT") from error
        except (ValueError, RuntimeError) as error:
            raise AppError(str(error), status_code=409, error_code="WHOLE_BOOK_REQUEST_REJECTED") from error

    @router.post(
        "/uploads/{upload_id}/courseware/whole-book",
        response_model=WholeBookRunView,
        status_code=202,
    )
    def start(upload_id: str, payload: WholeBookStartRequest, request: Request) -> WholeBookRunView:
        return call(service.start, upload_id, payload, actor_id=actor_id(request))

    @router.get("/uploads/{upload_id}/courseware/whole-book", response_model=WholeBookRunView)
    def latest(upload_id: str, request: Request) -> WholeBookRunView:
        actor_id(request)
        run = call(service.latest_for_upload, upload_id)
        if run is None:
            raise AppError("尚未开始整本教材制作", status_code=404, error_code="WHOLE_BOOK_NOT_STARTED")
        return run

    @router.get("/courseware/whole-book-runs/{run_id}", response_model=WholeBookRunView)
    def status(run_id: str, request: Request) -> WholeBookRunView:
        actor_id(request)
        return call(service.get_run, run_id)

    @router.post(
        "/courseware/whole-book-runs/{run_id}/pause",
        response_model=WholeBookRunView,
        status_code=202,
    )
    def pause(run_id: str, payload: WholeBookControlRequest, request: Request) -> WholeBookRunView:
        return call(service.pause, run_id, command_id=payload.commandId, actor_id=actor_id(request))

    @router.post(
        "/courseware/whole-book-runs/{run_id}/resume",
        response_model=WholeBookRunView,
        status_code=202,
    )
    def resume(run_id: str, payload: WholeBookControlRequest, request: Request) -> WholeBookRunView:
        return call(service.resume, run_id, command_id=payload.commandId, actor_id=actor_id(request))

    return router
