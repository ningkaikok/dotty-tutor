"""Capture and apply the non-secret Runtime selection pinned to a queued job."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from typing import Any, Iterator

from infrastructure.runtime.model_runtime import runtime as model_runtime
from infrastructure.runtime.ocr_runtime import runtime as ocr_runtime
from infrastructure.runtime.review_runtime import runtime_reviewer
from observability import log_event


def current_job_runtime_snapshot() -> dict[str, Any]:
    """Return only provider/model choices; credentials remain in worker environment."""
    return {
        "version": 1,
        "generation": {
            "provider": model_runtime.selection.provider,
            "model": model_runtime.selection.model,
        },
        "ocr": {"provider": ocr_runtime.selection.provider},
        "review": {"provider": runtime_reviewer.text_provider, "model": runtime_reviewer.text_model},
    }


@contextmanager
def use_job_runtime_snapshot(payload: dict[str, Any]) -> Iterator[None]:
    """Apply an enqueued selection in task-local contexts and restore it on every exit."""
    snapshot = payload.get("runtimeSnapshot")
    if not isinstance(snapshot, dict):
        log_event("runtime.job.snapshot_missing", level=30, compatibility="process-default")
        yield
        return
    if snapshot.get("version") != 1:
        raise ValueError("后台任务的 Runtime 配置快照版本不受支持")
    generation = snapshot.get("generation")
    ocr = snapshot.get("ocr")
    review = snapshot.get("review")
    if not (
        isinstance(generation, dict)
        and generation.get("provider")
        and generation.get("model")
        and isinstance(ocr, dict)
        and ocr.get("provider")
        and isinstance(review, dict)
        and review.get("provider")
        and review.get("model")
    ):
        raise ValueError("后台任务的 Runtime 配置快照不完整")
    with ExitStack() as stack:
        stack.enter_context(model_runtime.use_selection(
            str(generation["provider"]), str(generation["model"]),
        ))
        stack.enter_context(ocr_runtime.use_selection(str(ocr["provider"])))
        stack.enter_context(runtime_reviewer.use_selection(
            str(review["provider"]), str(review["model"]),
        ))
        yield
