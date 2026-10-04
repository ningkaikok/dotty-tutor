"""Durable AI chapter-draft jobs backed by the shared Worker."""

from __future__ import annotations

from typing import Any, Callable

from application.job_worker import (
    JobCancelled,
    JobFailure,
    RetryableJobError,
    TaskRegistry,
    TerminalJobError,
)


def build_chapter_registry(service: Any) -> TaskRegistry:
    """Register the source-versioned chapter generation handler."""
    registry = TaskRegistry()

    @registry.decorator("chapter.lesson.generate")
    def generate(payload: dict[str, Any], cancellation_check: Callable[[], bool]) -> dict[str, Any]:
        if cancellation_check():
            raise JobCancelled()
        try:
            return service.run_ai_generation(payload, cancellation_check=cancellation_check)
        except JobFailure:
            raise
        except LookupError as error:
            raise TerminalJobError(str(error)) from error
        except ValueError as error:
            raise TerminalJobError(str(error)) from error
        except Exception as error:
            raise RetryableJobError(f"章节草稿生成失败：{error}") from error

    @registry.decorator("material.courses.create")
    def create_material_courses(payload: dict[str, Any], cancellation_check: Callable[[], bool]) -> dict[str, Any]:
        from application.services.material_courses import MaterialCourseService
        from infrastructure.runtime.job_snapshot import use_job_runtime_snapshot
        from infrastructure.runtime.ocr_runtime import runtime
        try:
            # Automatic course preparation uses page routing even for legacy queued snapshots.
            with use_job_runtime_snapshot(payload), runtime.use_selection("auto"):
                return MaterialCourseService(service, runtime).run(payload["uploadId"], cancellation_check)
        except (ValueError, LookupError) as error:
            raise TerminalJobError(str(error)) from error

    return registry
