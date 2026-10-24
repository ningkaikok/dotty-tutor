"""Choose and prepare the next student task from persisted learning evidence."""

from __future__ import annotations

import time
from typing import Any

from application.services.review_task_start import start_review_task
from observability import log_event


class ContinuousLearningConflict(ValueError):
    """A selected task changed before the explicit start could apply."""

def _is_published_mistake(item: dict[str, Any]) -> bool:
    return item.get("contentType") == "application/vnd.dotty.publication+json"


def _next_free_publication(learning_store: Any, learner_id: str) -> dict[str, Any] | None:
    """Offer an incomplete free paper and its latest server-owned session pointer."""
    if not hasattr(learning_store, "list_free_publication_progress"):
        return None
    progress = {
        item["publicationId"]: item
        for item in learning_store.list_free_publication_progress(learner_id)
    }
    # Filter all summaries before ranking or capping. A completed first page of
    # publications must not hide a later unfinished paper.
    publications = learning_store.list_publications(status="published")
    chapter_publication_ids: set[str] = set()
    if hasattr(learning_store, "list_chapters"):
        chapter_publication_ids = {
            publication.get("publicationId")
            for chapter in learning_store.list_chapters()
            for publication in chapter.get("publications", [])
        }
    candidates = []
    for summary in publications:
        publication_id = summary.get("publicationId")
        lesson_count = int(summary.get("lessonCount") or len(summary.get("lessonIds") or []))
        if not publication_id or publication_id in chapter_publication_ids or lesson_count <= 0:
            continue
        try:
            detail = learning_store.load_publication(publication_id)
        except (LookupError, ValueError):
            continue
        if not detail or learning_store.is_chapter_publication(publication_id):
            continue
        if any(
            (lesson.get("questionPayload") or {}).get("question", {}).get("subject") == "english"
            for lesson in detail.get("lessons", [])
        ):
            continue
        item_progress = progress.get(publication_id) or {}
        attempted_count = int(item_progress.get("attemptedCount") or 0)
        if attempted_count >= lesson_count:
            continue
        candidates.append({
            "kind": "practice",
            "publicationId": publication_id,
            "title": summary.get("title") or "自由练习",
            "attemptedCount": attempted_count,
            "questionCount": lesson_count,
            "sessionId": item_progress.get("sessionId"),
            "lastActivityAt": float(item_progress.get("lastActivityAt") or 0),
            "publishedAt": float(summary.get("updatedAt") or 0),
        })
    if not candidates:
        return None
    candidates.sort(key=lambda item: (
        int(item["attemptedCount"] > 0),
        item["lastActivityAt"] if item["attemptedCount"] else item["publishedAt"],
    ), reverse=True)
    selected = candidates[0]
    selected.pop("lastActivityAt", None)
    selected.pop("publishedAt", None)
    return selected


def select_next_learning_action(
    *,
    learner_id: str,
    assignment_store: Any,
    learning_store: Any,
    mistake_store: Any,
    review_store: Any,
    now: float,
) -> dict[str, Any]:
    """Apply the fixed resume → confirm → correct → due review → new work order."""
    assignments = assignment_store.list_assignments_for_learner(learner_id, now=now)
    mistakes = mistake_store.list(learner_id)
    reviews = review_store.list_for_learner(learner_id)

    active_assignments = [
        item for item in assignments
        if item.get("learnerStatus") in {"in_progress", "overdue"}
        and int(item.get("attemptedCount") or 0) > 0
    ]
    if active_assignments:
        item = active_assignments[0]
        return {
            "kind": "assignment",
            "assignmentId": item["assignmentId"],
            "publicationId": item["publicationId"],
            "title": item.get("title") or "继续作业",
            "learnerStatus": item.get("learnerStatus"),
            "attemptedCount": int(item.get("attemptedCount") or 0),
            "questionCount": int(item.get("questionCount") or 0),
            "sessionId": item.get("sessionId"),
        }

    pending_confirmation = [
        item for item in mistakes
        if item.get("status") == "pending_confirmation" and not _is_published_mistake(item)
    ]
    if pending_confirmation:
        item = pending_confirmation[0]
        return {
            "kind": "confirm_mistake",
            "mistakeId": item["mistakeId"],
            "title": item.get("knowledgePoint") or "核对新错题",
        }

    correctable = [
        item for item in mistakes
        if item.get("status") == "unmastered"
        or (item.get("status") == "pending_confirmation" and _is_published_mistake(item))
    ]
    if correctable:
        item = correctable[0]
        return {
            "kind": "correct_mistake",
            "mistakeId": item["mistakeId"],
            "title": item.get("knowledgePoint") or "订正错题",
            "legacyPublishedPending": item.get("status") == "pending_confirmation" and _is_published_mistake(item),
        }

    due_reviews = [
        item for item in reviews
        if item.get("status") in {"scheduled", "generating", "ready"}
        and float(item.get("dueAt") or 0) <= now
    ]
    if due_reviews:
        item = min(due_reviews, key=lambda value: (float(value.get("dueAt") or 0), str(value.get("taskId") or "")))
        mistake = mistake_store.get(item["mistakeId"])
        return {
            "kind": "review",
            "taskId": item["taskId"],
            "mistakeId": item["mistakeId"],
            "title": (mistake or {}).get("knowledgePoint") or "到期复习",
            "status": item["status"],
            "dueAt": item["dueAt"],
        }

    new_assignments = [
        item for item in assignments
        if item.get("learnerStatus") in {"not_started", "overdue"}
        and int(item.get("attemptedCount") or 0) == 0
    ]
    if new_assignments:
        item = new_assignments[0]
        return {
            "kind": "assignment",
            "assignmentId": item["assignmentId"],
            "publicationId": item["publicationId"],
            "title": item.get("title") or "开始作业",
            "learnerStatus": item.get("learnerStatus"),
            "attemptedCount": 0,
            "questionCount": int(item.get("questionCount") or 0),
            "sessionId": item.get("sessionId"),
        }

    return _next_free_publication(learning_store, learner_id) or {"kind": "none"}


def continuous_learning_snapshot(
    learner_id: str,
    *,
    assignment_store: Any,
    learning_store: Any,
    mistake_store: Any,
    review_store: Any,
    now: float | None = None,
) -> dict[str, Any]:
    """Read a summary and next action without creating sessions or model work."""
    server_time = time.time() if now is None else now
    assignments = assignment_store.list_assignments_for_learner(learner_id, now=server_time)
    mistakes = mistake_store.list(learner_id)
    reviews = review_store.list_for_learner(learner_id)
    due_reviews = [
        item for item in reviews
        if item.get("status") in {"scheduled", "generating", "ready"}
        and float(item.get("dueAt") or 0) <= server_time
    ]
    pending = [
        item for item in mistakes
        if item.get("status") == "pending_confirmation" and not _is_published_mistake(item)
    ]
    unmastered = [
        item for item in mistakes
        if item.get("status") == "unmastered"
        or (item.get("status") == "pending_confirmation" and _is_published_mistake(item))
    ]
    return {
        "learnerId": learner_id,
        "serverTime": server_time,
        "next": select_next_learning_action(
            learner_id=learner_id,
            assignment_store=assignment_store,
            learning_store=learning_store,
            mistake_store=mistake_store,
            review_store=review_store,
            now=server_time,
        ),
        "summary": {
            "unfinishedAssignments": sum(item.get("learnerStatus") != "completed" for item in assignments),
            "pendingConfirmationCount": len(pending),
            "unmasteredCount": len(unmastered),
            "dueReviewCount": len(due_reviews),
        },
    }


def start_continuous_learning(
    learner_id: str,
    *,
    assignment_store: Any,
    learning_store: Any,
    mistake_store: Any,
    review_store: Any,
    variation_service: Any,
    now: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Honor explicit start intent and prepare at most one selected review question."""
    snapshot = continuous_learning_snapshot(
        learner_id,
        assignment_store=assignment_store,
        learning_store=learning_store,
        mistake_store=mistake_store,
        review_store=review_store,
        now=now,
    )
    next_item = snapshot["next"]
    if next_item.get("kind") == "correct_mistake" and next_item.get("legacyPublishedPending"):
        activate = getattr(mistake_store, "activate_published_attempt", None)
        if activate is None:
            raise RuntimeError("已发布错题状态暂不可恢复")
        upgraded = activate(next_item["mistakeId"], learner_id)
        if not upgraded or upgraded.get("status") != "unmastered":
            raise ContinuousLearningConflict("错题状态已变化，请刷新")
        log_event("learning.continuous.published_mistake_activated", mistake_id=next_item["mistakeId"])
        snapshot = continuous_learning_snapshot(
            learner_id,
            assignment_store=assignment_store,
            learning_store=learning_store,
            mistake_store=mistake_store,
            review_store=review_store,
            now=now,
        )
        next_item = snapshot["next"]

    if next_item.get("kind") != "review":
        return snapshot, None
    task = start_review_task(
        task_id=next_item["taskId"],
        learner_id=learner_id,
        mistake_store=mistake_store,
        review_store=review_store,
        variation_service=variation_service,
        now=time.time() if now is None else now,
    )
    snapshot["next"] = {
        "kind": "review",
        "taskId": task["taskId"],
        "mistakeId": task["mistakeId"],
        "status": task["status"],
        "dueAt": task["dueAt"],
    }
    return snapshot, task
