"""Register lesson media rendering with the shared durable Worker."""

from __future__ import annotations

from typing import Any, Callable

from application.job_worker import (
    JobCancelled,
    JobFailure,
    RetryableJobError,
    TaskRegistry,
    TerminalJobError,
)


def build_lesson_media_registry(service: Any) -> TaskRegistry:
    """Create the bounded render handler for the shared Worker registry."""
    registry = TaskRegistry()

    @registry.decorator("lesson_media.render")
    def render(payload: dict[str, Any], cancellation_check: Callable[[], bool]) -> Any:
        if cancellation_check():
            raise JobCancelled()
        try:
            return service.run_render(payload, cancellation_check)
        except JobFailure:
            raise
        except (LookupError, ValueError) as error:
            raise TerminalJobError(str(error)) from error
        except Exception as error:
            raise RetryableJobError(f"教学视频生成失败：{type(error).__name__}") from error

    return registry


__all__ = ["build_lesson_media_registry"]
