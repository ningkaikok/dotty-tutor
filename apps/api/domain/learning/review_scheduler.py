"""Pure scheduling decisions for spaced review tasks."""

from __future__ import annotations

import re
from typing import Any

from domain.learning.mastery_policy import MasteryPolicy

_RETRY_VERSION = re.compile(r"^(?P<base>.+):retry-(?P<number>[1-9][0-9]*)$")


def initial_schedule(
    policy: MasteryPolicy,
    *,
    base_time: float,
    trigger_evidence_ref: str | None = None,
) -> list[dict[str, Any]]:
    """Build the first schedule, preserving an auditable sequence number."""
    return [
        {
            "intervalDays": interval,
            "dueAt": base_time + interval * 86_400,
            "scheduleVersion": policy.policy_version,
            "sequenceNo": index + 1,
            "profile": policy.profile,
            "triggerEvidenceRef": trigger_evidence_ref,
            "supersededAt": None,
        }
        for index, interval in enumerate(policy.intervals_days)
    ]


def follow_up_schedule(
    policy: MasteryPolicy,
    *,
    base_time: float,
    sequence_no: int,
    assessment: str,
    next_action: str,
    schedule_version: str | None = None,
    trigger_evidence_ref: str | None = None,
) -> dict[str, Any] | None:
    """Choose the next task after an answer.

    Correct answers advance through longer intervals.  An incorrect answer
    starts a new schedule version at the retry interval; historical tasks are
    superseded by the persistence layer, never deleted.
    """
    normalized_assessment = str(assessment or "").strip().lower()
    if next_action in {"needs_review", "test_out"}:
        return None
    # Unknown historical points intentionally keep the old fixed schedule:
    # their rows predate policy metadata, so a wrong answer must not rewrite
    # future 1/3/7 tasks during migration.
    if policy.gate_mode == "legacy" and normalized_assessment != "correct":
        return None
    if normalized_assessment == "correct" and next_action in {"advance", "stay"}:
        next_sequence = sequence_no + 1
        # A correct answer that has not passed the domain gate still needs a
        # future evidence opportunity. Once the configured ladder is exhausted,
        # repeat its final interval instead of silently creating a review gap.
        interval = policy.intervals_days[min(next_sequence - 1, len(policy.intervals_days) - 1)]
        version = schedule_version or policy.policy_version
    elif normalized_assessment != "correct":
        next_sequence = 1
        interval = policy.retry_interval_days
        current_version = schedule_version or policy.policy_version
        match = _RETRY_VERSION.fullmatch(current_version)
        if match:
            if match.group("base") != policy.policy_version:
                raise ValueError("scheduleVersion 不属于当前 policy")
            retry_number = int(match.group("number")) + 1
        else:
            if current_version != policy.policy_version:
                raise ValueError("scheduleVersion 不是受支持的 retry 版本")
            retry_number = sequence_no + 1
        # Retry versions are always derived from the registered base policy;
        # never append a suffix to an already suffixed version.
        version = f"{policy.policy_version}:retry-{retry_number}"
    else:
        return None
    return {
        "intervalDays": interval,
        "dueAt": base_time + interval * 86_400,
        "scheduleVersion": version,
        "sequenceNo": next_sequence,
        "profile": policy.profile,
        "triggerEvidenceRef": trigger_evidence_ref,
        "supersedeFuture": normalized_assessment != "correct",
        "supersededAt": None,
    }


def schedule_review(
    policy: MasteryPolicy,
    *,
    base_time: float,
    sequence_no: int = 0,
    assessment: str | None = None,
    next_action: str = "advance",
    trigger_evidence_ref: str | None = None,
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """Compatibility facade for initial and follow-up scheduling."""
    if assessment is None:
        return initial_schedule(policy, base_time=base_time, trigger_evidence_ref=trigger_evidence_ref)
    return follow_up_schedule(
        policy,
        base_time=base_time,
        sequence_no=sequence_no,
        assessment=assessment,
        next_action=next_action,
        trigger_evidence_ref=trigger_evidence_ref,
    )
