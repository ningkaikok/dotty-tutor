"""Deterministic routing around AI pre-review verdicts.

The model's verdict is advisory. Which pages a teacher must read, which may be
bulk-confirmed, and how reliable the reviewer has been are decided here so they
are reproducible and testable without any model call.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Page fields a teacher actually reviews; workflow state is excluded so a status
# change alone never invalidates a verdict.
_REVIEWED_PAGE_FIELDS = (
    "title", "pageKind", "layout", "objectiveIds", "sourceRefs", "blocks", "teacherNotes", "revealSteps",
)


def reviewable_page(page: dict[str, Any]) -> dict[str, Any]:
    """Project a courseware page to the content the reviewer is asked to judge."""
    return {key: page.get(key) for key in _REVIEWED_PAGE_FIELDS}


def input_hash(subject: dict[str, Any], sources: list[dict[str, Any]], *, prompt_version: str,
               reviewer: dict[str, str]) -> str:
    """Identity of everything sent to the reviewer; equal hashes never re-run the model.

    ``subject`` must already be the projected content (e.g. ``reviewable_page``).
    """
    payload = {
        "subject": subject, "sources": sources, "prompt": prompt_version,
        "reviewer": {"provider": reviewer["provider"], "model": reviewer["model"]},
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def is_sampled(courseware_id: str, page_id: str, page_version: int, rate: float) -> bool:
    """Stable spot-check draw: the same page version is always sampled or never."""
    if rate >= 1:
        return True
    if rate <= 0:
        return False
    digest = hashlib.sha256(f"{courseware_id}:{page_id}:{page_version}".encode()).hexdigest()
    return int(digest[:8], 16) / 0x1_0000_0000 < rate


def route_page(page: dict[str, Any], review: dict[str, Any] | None, *, sampled: bool) -> str:
    """Decide where a page goes; a teacher decision always wins over the AI verdict."""
    if page.get("reviewStatus") in {"approved", "changes_requested"}:
        return "decided"
    if review is None:
        return "not_reviewed"
    if int(review["subjectVersion"]) != int(page["version"]):
        return "stale"
    if review["verdict"] != "pass" or sampled:
        return "needs_review"
    return "bulk_confirmable"


def agreement_metrics(pages: list[dict[str, Any]], reviews: dict[str, dict[str, Any]],
                      records: list[dict[str, Any]], *, courseware_id: str, rate: float) -> dict[str, int]:
    """Compare AI verdicts with teacher decisions that followed them on the same version.

    Only pages a teacher was routed to read (flagged or sampled) count: bulk-confirmed
    unsampled passes were not independently checked and would inflate agreement.
    """
    metrics = {"compared": 0, "agreements": 0, "missed": 0, "falseAlarms": 0}
    for page in pages:
        review = reviews.get(page["pageId"])
        if review is None or int(review["subjectVersion"]) != int(page["version"]):
            continue
        sampled = is_sampled(courseware_id, page["pageId"], int(page["version"]), rate)
        if review["verdict"] == "pass" and not sampled:
            continue
        later = [record for record in records
                 if page["pageId"] in (record.get("pageIds") or []) and float(record.get("createdAt", 0)) > review["createdAt"]]
        if not later:
            continue
        teacher_approved = later[-1].get("decision") == "approve"
        ai_passed = review["verdict"] == "pass"
        metrics["compared"] += 1
        if ai_passed == teacher_approved:
            metrics["agreements"] += 1
        elif ai_passed:
            metrics["missed"] += 1
        else:
            metrics["falseAlarms"] += 1
    return metrics
