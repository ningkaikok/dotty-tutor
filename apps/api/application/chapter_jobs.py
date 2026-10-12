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


def build_chapter_registry(service: Any, source_preparation_service: Any | None = None) -> TaskRegistry:
    """Register legacy chapter generation and fenced textbook-source preparation."""
    registry = TaskRegistry()

    @registry.decorator("chapter.lesson.generate")
    def generate(payload: dict[str, Any], cancellation_check: Callable[[], bool], context: Any) -> dict[str, Any]:
        if cancellation_check():
            raise JobCancelled()
        try:
            return service.run_ai_generation(
                payload, cancellation_check=cancellation_check, execution_context=context,
            )
        except JobFailure:
            raise
        except LookupError as error:
            raise TerminalJobError(str(error)) from error
        except ValueError as error:
            raise TerminalJobError(str(error)) from error
        except Exception as error:
            raise RetryableJobError(f"章节草稿生成失败：{error}") from error

    if source_preparation_service is not None:
        @registry.decorator("courseware.source.prepare")
        def prepare_courseware_source(
            payload: dict[str, Any],
            cancellation_check: Callable[[], bool],
            context: Any,
        ) -> Any:
            try:
                return source_preparation_service.run(payload, cancellation_check, context)
            except JobCancelled:
                raise
            except (ValueError, LookupError) as error:
                raise TerminalJobError(str(error)) from error
            except Exception as error:
                raise RetryableJobError(f"教材来源准备失败：{error}") from error

    return registry
