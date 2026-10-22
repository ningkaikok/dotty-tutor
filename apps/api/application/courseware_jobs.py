"""Worker handlers for source-grounded courseware generation stages."""

from __future__ import annotations

from typing import Any, Callable

from application.job_worker import (
    JobCancelled,
    RetryableJobError,
    TaskRegistry,
    TerminalJobError,
)
from persistence.courseware_store import CoursewareStaleExecution
from prompts import (
    PromptTemplate,  # noqa: F401 - validates frozen prompt module at startup
)


def build_courseware_registry(service: Any) -> TaskRegistry:
    """Register plan and page generation with the shared fenced Worker context."""
    registry = TaskRegistry()

    @registry.decorator("courseware.plan.generate")
    def generate_plan(
        payload: dict[str, Any], cancellation_check: Callable[[], bool], context: Any,
    ) -> dict[str, Any]:
        try:
            return service.run_plan_generation(payload, cancellation_check, context)
        except JobCancelled:
            raise
        except (ValueError, LookupError, CoursewareStaleExecution) as error:
            raise TerminalJobError(str(error)) from error
        except Exception as error:
            raise RetryableJobError(f"课件规划生成失败：{error}") from error

    @registry.decorator("courseware.document.generate")
    def generate_document(
        payload: dict[str, Any], cancellation_check: Callable[[], bool], context: Any,
    ) -> dict[str, Any]:
        try:
            return service.run_document_generation(payload, cancellation_check, context)
        except JobCancelled:
            raise
        except (ValueError, LookupError, CoursewareStaleExecution) as error:
            raise TerminalJobError(str(error)) from error
        except Exception as error:
            raise RetryableJobError(f"课件页面生成失败：{error}") from error

    @registry.decorator("courseware.automation")
    def complete_automatically(payload: dict[str, Any], cancellation_check: Callable[[], bool], context: Any) -> dict[str, Any]:
        if service.automation is None:
            raise TerminalJobError("自动课件服务尚未就绪")
        try:
            return service.automation.run(payload, cancellation_check, context)
        except JobCancelled:
            raise
        except Exception as error:
            raise RetryableJobError(f"自动制作检查失败：{error}") from error

    return registry
