"""Bounded preparation advice over current-source coverage, with no workflow writes."""
from __future__ import annotations

from typing import Any


def completion_report(
    chapter: dict[str, Any], source: dict[str, Any], coverage: dict[str, Any], *,
    plan: dict[str, Any] | None, blocked_pages: list[int], active_generation: bool,
    max_pages_per_batch: int,
) -> dict[str, Any]:
    """Split only ready, ungenerated, unplanned physical pages into consecutive ranges.

    Suggestions never authorize replacement of an existing current-source plan.
    The chapter record version remains necessary when a teacher later starts the
    ordinary planning workflow; reading this report performs no generation.
    """
    if type(max_pages_per_batch) is not int or not 1 <= max_pages_per_batch <= 20:
        raise ValueError("每批补齐建议必须在 1 到 20 个来源原页之间")
    required = set(range(int(source["pageStart"]), int(source["pageEnd"]) + 1))
    blocked = set(blocked_pages) & required
    generated = set(coverage["generatedPages"]) & required
    planned = set(coverage["plannedPages"]) & required
    reviewed = set(coverage["reviewedPages"]) & required
    published = set(coverage["publishedPages"]) & required
    existing = None
    if plan:
        existing = {key: plan[key] for key in (
            "sourceRevisionId", "planVersion", "recordVersion", "status",
            "generationStatus", "pageStart", "pageEnd",
        )}
        existing["stale"] = plan["sourceRevisionId"] != source["sourceRevisionId"] or plan["status"] == "superseded"
    current_plan = existing is not None and not existing["stale"]
    candidates = sorted(required - blocked - generated - planned)
    batches: list[dict[str, Any]] = []
    for number in candidates:
        if (batches and number == batches[-1]["pageEnd"] + 1
                and len(batches[-1]["sourcePages"]) < max_pages_per_batch):
            batches[-1]["pageEnd"] = number
            batches[-1]["sourcePages"].append(number)
        else:
            batches.append({"pageStart": number, "pageEnd": number, "sourcePages": [number]})
    if active_generation:
        action = "wait_generation"
    elif current_plan:
        action = "continue_plan"
    elif batches:
        action = "create_plan"
    elif generated - reviewed or generated - published:
        action = "review_generated"
    elif blocked:
        action = "repair_source"
    else:
        action = "complete"
    return {
        "chapterId": chapter["chapterId"], "sourceRevisionId": source["sourceRevisionId"],
        "expectedRecordVersion": int(chapter["recordVersion"]), "maxPagesPerBatch": max_pages_per_batch,
        "batches": batches, "blockedPages": sorted(blocked), "alreadyGeneratedPages": sorted(generated),
        "alreadyPlannedPages": sorted(planned), "pendingGenerationPages": sorted(planned - generated - blocked),
        "pendingReviewPages": sorted(generated - reviewed), "pendingPublicationPages": sorted(generated - published),
        "existingPlan": existing, "canCreatePlan": bool(batches) and not current_plan and not active_generation,
        "action": action, "requiresHumanReview": True,
    }
