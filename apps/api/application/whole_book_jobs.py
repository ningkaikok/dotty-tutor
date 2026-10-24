"""Worker registration and completion notifications for whole-book runs."""

from __future__ import annotations

from typing import Any, Callable

from application.job_worker import (
    JobCancelled,
    RetryableJobError,
    TaskRegistry,
    TerminalJobError,
)
from application.services.whole_book import (
    ADVANCE_JOB_TYPE,
    CONTROL_JOB_TYPE,
    START_JOB_TYPE,
)

_NOTIFIED_TYPES = frozenset({
    "courseware.source.prepare",
    "courseware.plan.generate",
    "courseware.document.generate",
    "courseware.automation",
})


def build_whole_book_registry(service: Any) -> TaskRegistry:
    """Register short controller jobs; they never call a model themselves."""
    registry = TaskRegistry()

    @registry.decorator(START_JOB_TYPE)
    def start(payload: dict[str, Any], cancellation_check: Callable[[], bool], context: Any) -> dict[str, Any]:
        try:
            return service.advance(context.job_id, cancellation_check, context)
        except JobCancelled:
            raise
        except (ValueError, LookupError) as error:
            raise TerminalJobError(str(error)) from error
        except Exception as error:
            raise RetryableJobError(f"整本教材制作无法继续：{error}") from error

    @registry.decorator(ADVANCE_JOB_TYPE)
    def advance(payload: dict[str, Any], cancellation_check: Callable[[], bool], context: Any) -> dict[str, Any]:
        try:
            return service.advance(str(payload["runId"]), cancellation_check, context)
        except JobCancelled:
            raise
        except (ValueError, LookupError) as error:
            raise TerminalJobError(str(error)) from error
        except Exception as error:
            raise RetryableJobError(f"整本教材制作无法继续：{error}") from error

    @registry.decorator(CONTROL_JOB_TYPE)
    def control(payload: dict[str, Any], _cancellation_check: Callable[[], bool], context: Any) -> dict[str, Any]:
        del context
        return service.run_control(payload, lambda: False, None)

    return registry


def add_whole_book_completion_hooks(registry: TaskRegistry, service: Any) -> TaskRegistry:
    """Notify a run after an existing source/courseware stage settles.

    The notification is a separate idempotent controller job. A controller
    that sees a queued child returns promptly; if a child is already running it
    waits under its own Worker lease until that child settles.
    """
    wrapped = TaskRegistry()
    for job_type, handler in registry.items():
        if job_type not in _NOTIFIED_TYPES:
            wrapped.register(job_type, handler)
            continue

        def with_notification(original: Any, settled_type: str) -> Any:
            def execute(payload: dict[str, Any], cancellation_check: Callable[[], bool], context: Any) -> Any:
                try:
                    return original(payload, cancellation_check, context)
                finally:
                    try:
                        service.notify_after_job(context.job_id, context.attempt_count)
                    except Exception as error:
                        # These handlers all persist their useful result by job
                        # ID, so the existing bounded retry reuses checkpoints.
                        raise RetryableJobError(
                            f"{settled_type} 已完成，但无法接续整本制作：{error}",
                        ) from error
            return execute

        wrapped.register(job_type, with_notification(handler, job_type))
    return wrapped
