"""Deterministic mastery gates and objective-specific policy selection.

The policy layer is intentionally free of persistence and model calls.  It turns
observable evidence into a small, versioned decision that the review scheduler
and API can both audit.  Missing metadata resolves to the legacy profile so
existing rows keep their historical 1/3/7-day behaviour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

ObjectiveType = Literal["memory", "procedural", "conceptual", "design", "unknown"]
GateMode = Literal["quantitative", "qualitative", "legacy"]
NextAction = Literal["stay", "advance", "test_out", "needs_review"]

POLICY_VERSION = "mastery-policy-v1"
LEGACY_POLICY_VERSION = "legacy-1-3-7-v1"
OBJECTIVE_TYPES: frozenset[str] = frozenset({"memory", "procedural", "conceptual", "design", "unknown"})
GATE_MODES: frozenset[str] = frozenset({"quantitative", "qualitative", "legacy"})
_RETRY_VERSION = re.compile(r"^(mastery-policy-v1):retry-[1-9][0-9]*$")


@dataclass(frozen=True)
class MasteryPolicy:
    """A review profile with explicit spacing and gate requirements."""

    objective_type: ObjectiveType
    gate_mode: GateMode
    policy_version: str
    intervals_days: tuple[int, ...]
    quantitative_accuracy: float = 0.90
    quantitative_min_evidence: int = 2
    qualitative_min_confidence: float = 0.80
    retry_interval_days: int = 1

    @property
    def profile(self) -> str:
        return f"{self.objective_type}:{self.gate_mode}"


_POLICIES: dict[ObjectiveType, MasteryPolicy] = {
    "memory": MasteryPolicy("memory", "quantitative", POLICY_VERSION, (1, 3, 7, 14, 30)),
    "procedural": MasteryPolicy("procedural", "quantitative", POLICY_VERSION, (1, 3, 7, 14, 30)),
    "conceptual": MasteryPolicy("conceptual", "qualitative", POLICY_VERSION, (2, 5, 10, 21, 45)),
    "design": MasteryPolicy("design", "qualitative", POLICY_VERSION, (3, 7, 14, 30, 60)),
    "unknown": MasteryPolicy("unknown", "legacy", LEGACY_POLICY_VERSION, (1, 3, 7)),
}


def _normalized(value: object, allowed: frozenset[str], fallback: str, *, field: str) -> str:
    raw = getattr(value, "value", value)
    normalized = str(raw or "").strip().lower()
    if not normalized:
        return fallback
    if normalized not in allowed:
        raise ValueError(f"unsupported {field}: {normalized}")
    return normalized


def _canonical_policy_version(value: object, *, expected: str) -> str:
    """Accept only a registered policy version (or a generated retry suffix)."""
    raw = str(value or "").strip()
    if not raw:
        return expected
    if raw == expected:
        return raw
    if expected == POLICY_VERSION and _RETRY_VERSION.fullmatch(raw):
        return POLICY_VERSION
    raise ValueError(f"unsupported policyVersion: {raw}")


def resolve_policy(
    objective_type: object = None,
    gate_mode: object = None,
    policy_version: object = None,
) -> MasteryPolicy:
    """Resolve metadata with a closed objective/gate/version allowlist.

    Missing metadata is the explicit legacy fallback.  A supplied typed
    objective cannot silently change gate mode or select an unregistered
    policy version; malformed persisted content therefore fails closed.
    """
    objective = _normalized(objective_type, OBJECTIVE_TYPES, "unknown", field="objectiveType")
    selected = _POLICIES[objective]  # type: ignore[index]
    mode = _normalized(gate_mode, GATE_MODES, selected.gate_mode, field="gateMode")
    if mode != selected.gate_mode:
        raise ValueError(
            f"gateMode {mode} is not supported for objectiveType {objective}; "
            f"expected {selected.gate_mode}"
        )
    version = _canonical_policy_version(policy_version, expected=selected.policy_version)
    if version != selected.policy_version:
        raise ValueError(
            f"policyVersion {version} is not supported for objectiveType {objective}"
        )
    if mode == selected.gate_mode and version == selected.policy_version:
        return selected
    raise AssertionError("registered policy mapping is inconsistent")


def policy_for_knowledge_point(item: Mapping[str, object] | None) -> MasteryPolicy:
    """Resolve a knowledge-point row or embedded projection into a policy."""
    row = item or {}
    return resolve_policy(row.get("objective_type", row.get("objectiveType")), row.get("gate_mode", row.get("gateMode")), row.get("policy_version", row.get("policyVersion")))


def _float(value: object, default: float = 0.0) -> float:
    try:
        return float(str(value)) if value is not None else default
    except (TypeError, ValueError):
        return default


def _int(value: object, default: int = 0) -> int:
    try:
        return int(str(value)) if value is not None else default
    except (TypeError, ValueError):
        return default


def _refs(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [str(item) for item in value if str(item).strip()]


def evaluate_gate(
    policy: MasteryPolicy,
    *,
    accuracy: float | None = None,
    evidence_count: int | None = None,
    rubric_passed: bool | None = None,
    confidence: float | None = None,
    evidence_refs: Sequence[str] | None = None,
    assessment: str | None = None,
) -> dict[str, Any]:
    """Return a deterministic gate result and the next action.

    Quantitative gates need both accuracy and at least two pieces of evidence.
    Qualitative gates need a passing rubric, sufficient confidence, and
    non-empty evidence references. Missing evidence is never promoted.
    """
    count = _int(evidence_count)
    score = _float(accuracy)
    refs = _refs(evidence_refs)
    confidence_value = _float(confidence)
    assessment_value = str(assessment or "").strip().lower()
    if policy.gate_mode == "legacy":
        passed = assessment_value == "correct"
        action: NextAction = "advance" if passed else "stay"
        if not assessment_value and count == 0:
            action = "needs_review"
        return {
            "mode": policy.gate_mode,
            "passed": passed,
            "accuracy": score,
            "evidenceCount": count,
            "evidenceRefs": refs,
            "confidence": confidence_value,
            "nextAction": action,
        }

    if policy.gate_mode == "quantitative":
        enough_evidence = count >= policy.quantitative_min_evidence
        passed = score >= policy.quantitative_accuracy and enough_evidence
        action = "advance" if passed else "stay"
        if count == 0:
            action = "needs_review"
        return {
            "mode": policy.gate_mode,
            "passed": passed,
            "accuracy": score,
            "threshold": policy.quantitative_accuracy,
            "evidenceCount": count,
            "minimumEvidence": policy.quantitative_min_evidence,
            "evidenceRefs": refs,
            "confidence": confidence_value,
            "nextAction": action,
        }

    enough_confidence = confidence_value >= policy.qualitative_min_confidence
    passed = bool(rubric_passed) and enough_confidence and bool(refs)
    action = "advance" if passed else "stay"
    if not refs or confidence is None or confidence_value < policy.qualitative_min_confidence:
        action = "needs_review"
    return {
        "mode": policy.gate_mode,
        "passed": passed,
        "rubricPassed": bool(rubric_passed),
        "confidence": confidence_value,
        "minimumConfidence": policy.qualitative_min_confidence,
        "evidenceCount": count,
        "evidenceRefs": refs,
        "nextAction": action,
    }


def decide_next_action(
    policy: MasteryPolicy,
    *,
    accuracy: float | None = None,
    evidence_count: int | None = None,
    rubric_passed: bool | None = None,
    confidence: float | None = None,
    evidence_refs: Sequence[str] | None = None,
    assessment: str | None = None,
    sequence_no: int = 0,
) -> dict[str, Any]:
    """Add policy metadata and terminal ``test_out`` to a gate result."""
    gate = evaluate_gate(
        policy,
        accuracy=accuracy,
        evidence_count=evidence_count,
        rubric_passed=rubric_passed,
        confidence=confidence,
        evidence_refs=evidence_refs,
        assessment=assessment,
    )
    action = gate["nextAction"]
    if action == "advance" and sequence_no >= len(policy.intervals_days):
        action = "test_out"
    return {
        "policyVersion": policy.policy_version,
        "objectiveType": policy.objective_type,
        "gateMode": policy.gate_mode,
        "profile": policy.profile,
        "nextAction": action,
        "gate": gate,
    }


# Short aliases keep call sites readable and make the pure module convenient in
# migration/route tests without exposing persistence-specific names.
get_policy = resolve_policy
gate_decision = evaluate_gate
