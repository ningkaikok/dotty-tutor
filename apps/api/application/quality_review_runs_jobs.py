"""Register the one-attempt quality-review run handler with the shared Worker."""

from __future__ import annotations

from typing import Any, Callable

from application.job_worker import JobCancelled, TaskRegistry, TerminalJobError


def build_quality_review_runs_registry(service: Any) -> TaskRegistry:
    """Build the registry containing the bounded model-run job type."""
    registry = TaskRegistry()

    @registry.decorator("quality_review.run")
    def run(payload: dict[str, Any], cancellation_check: Callable[[], bool]) -> Any:
        run_id = str(payload.get("runId", ""))
        try:
            return service.run_job(payload, cancellation_check)
        except JobCancelled:
            raise
        except TerminalJobError:
            raise
        except Exception as error:
            service.fail_job(run_id, error)
            raise TerminalJobError("有界模型运行失败") from error

    return registry


__all__ = ["build_quality_review_runs_registry"]
