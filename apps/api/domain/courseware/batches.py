"""Which plan versions still own live courseware after incremental unit completion.

A chapter keeps one plan record. Supplementing it moves the approved plan into
``retainedBatches`` instead of discarding its courseware, so every "is this draft
still current" check must accept the open plan version and every retained one.
"""

from __future__ import annotations

from typing import Any


def active_plan_versions(plan: dict[str, Any] | None, source_revision_id: str) -> set[int]:
    """Return plan versions whose documents stay editable for this source revision."""
    if not plan or plan.get("sourceRevisionId") != source_revision_id:
        return set()
    versions = {int(plan.get("planVersion", 0))}
    versions.update(int(batch["planVersion"]) for batch in plan.get("retainedBatches") or [])
    return versions


def owns_document(plan: dict[str, Any] | None, document: dict[str, Any]) -> bool:
    """True when the document belongs to the open plan or a retained batch."""
    return int(document.get("planVersion", -1)) in active_plan_versions(
        plan, str(document.get("sourceRevisionId") or ""),
    )


def objectives_for_version(plan: dict[str, Any] | None, plan_version: int) -> list[dict[str, Any]]:
    """Objectives frozen with the batch that generated a document."""
    if not plan:
        return []
    if int(plan.get("planVersion", 0)) == plan_version:
        return list(plan.get("objectives") or [])
    for batch in plan.get("retainedBatches") or []:
        if int(batch["planVersion"]) == plan_version:
            return list(batch.get("objectives") or [])
    return []


def retained_page_ranges(plan: dict[str, Any] | None) -> set[int]:
    """Physical source pages already claimed by retained batches."""
    pages: set[int] = set()
    for batch in (plan or {}).get("retainedBatches") or []:
        pages.update(range(int(batch["pageStart"]), int(batch["pageEnd"]) + 1))
    return pages


def batches_after_supplement(plan: dict[str, Any] | None, source_revision_id: str) -> list[dict[str, Any]]:
    """Carry the approved open plan into the retained list before it is replaced.

    Only an approved plan on the same source revision can be retained; anything
    else has no confirmed courseware and is safely discarded as before.
    """
    if not plan or plan.get("sourceRevisionId") != source_revision_id:
        return []
    batches = [dict(batch) for batch in plan.get("retainedBatches") or []]
    if plan.get("status") == "approved" and plan.get("coursewareIds"):
        batches.append({
            "planVersion": int(plan["planVersion"]),
            "pageStart": int(plan["pageStart"]), "pageEnd": int(plan["pageEnd"]),
            "objectives": list(plan.get("objectives") or []),
            "coursewareIds": list(plan.get("coursewareIds") or []),
        })
    return batches
