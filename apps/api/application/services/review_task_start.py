"""Idempotent model preparation for one persisted spaced-review task."""

from __future__ import annotations

from typing import Any


class ReviewTaskStartConflict(ValueError):
    """The task is already being prepared or is no longer startable."""


def start_review_task(
    *,
    task_id: str,
    learner_id: str,
    mistake_store: Any,
    review_store: Any,
    variation_service: Any,
    now: float,
) -> dict[str, Any]:
    """Prepare or resume one review using a database claim before model work.

    ``scheduled -> generating`` is an atomic database transition, so concurrent
    requests cannot spend twice or replace the winning question. Stale claims
    are reclaimable after a bounded timeout; failed generation releases its
    claim so a later explicit retry can proceed.
    """
    task = review_store.get(task_id)
    if not task or task.get("learnerId") != learner_id:
        raise LookupError("复习任务不存在")
    if task.get("status") == "ready":
        return task
    if task.get("status") not in {"scheduled", "generating"}:
        raise ReviewTaskStartConflict("这项复习任务已经完成")

    claim = review_store.claim_start(task_id, now=now)
    if claim == "ready":
        return review_store.get(task_id) or task
    if claim != "claimed":
        raise ReviewTaskStartConflict("复习题正在准备，请稍候重试")

    mistake = mistake_store.get(task["mistakeId"])
    if not mistake or mistake.get("learnerId") != learner_id:
        review_store.release_start(task_id, claimed_at=now)
        raise LookupError("复习任务对应的错题不存在")
    try:
        generated = variation_service.generate(mistake, 3)
        started = review_store.finish_start(
            task_id,
            claimed_at=now,
            question_payload=generated["questionPayload"],
            model_run=generated["modelRun"],
        )
    except Exception:
        review_store.release_start(task_id, claimed_at=now)
        raise
    if not started:
        raise ReviewTaskStartConflict("复习任务状态已变化，请刷新")
    return started
