"""Worker handlers for advisory AI pre-review of courseware pages and source cases."""

from __future__ import annotations

from typing import Any, Callable

from pydantic import ValidationError

from application.job_worker import (
    JobCancelled,
    RetryableJobError,
    TaskRegistry,
    TerminalJobError,
)


def build_ai_review_registry(service: Any) -> TaskRegistry:
    """Register reviewer runs; malformed model output retries once, then fails visibly."""
    registry = TaskRegistry()

    def guarded(run: Callable[..., dict[str, Any]], label: str) -> Callable[..., dict[str, Any]]:
        def handler(payload: dict[str, Any], cancellation_check: Callable[[], bool], context: Any) -> dict[str, Any]:
            try:
                return run(payload, cancellation_check, context)
            except (JobCancelled, TerminalJobError):
                raise
            except (LookupError, ValueError) as error:
                if isinstance(error, ValidationError):
                    raise RetryableJobError(f"{label}输出不符合格式：{error}") from error
                raise TerminalJobError(str(error)) from error
            except Exception as error:
                raise RetryableJobError(f"{label}失败：{error}") from error
        return handler

    registry.decorator("courseware.ai_review")(guarded(service.run, "AI 初审"))
    registry.decorator("quality_review.ai_opinion")(guarded(service.run_case_opinion, "AI 参考意见"))
    return registry
