"""Bounded, evidence-backed learner profile facts for shadow experiments.

The profile is a projection, not a second mastery store.  Facts are scoped by
publication and knowledge-point identity, expire quickly, and never include
conversation text or inferred personality, family, mental-health, or sensitive
identity attributes.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

PROFILE_VERSION = "learner-profile-v1"
DEFAULT_TTLS = {
    "mastery": 7 * 86_400,
    "confirmed_mistake": 14 * 86_400,
    "hint_dependency": 3 * 86_400,
    "recent_practice": 3 * 86_400,
}
ALLOWED_KINDS = frozenset(DEFAULT_TTLS)
FORBIDDEN_INPUT_KEYS = frozenset({
    "content", "message", "messages", "conversation", "conversationtext", "personality",
    "family", "mentalhealth", "emotion", "email", "phone", "studentname", "learnername",
})


@dataclass(frozen=True)
class ProfileFact:
    kind: str
    publication_id: str
    subject_id: str
    value: dict[str, Any]
    evidence_ref: str
    observed_at: float
    expires_at: float
    profile_version: str = PROFILE_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "publicationId": self.publication_id,
            "subjectId": self.subject_id,
            "value": dict(self.value),
            "evidenceRef": self.evidence_ref,
            "observedAt": self.observed_at,
            "expiresAt": self.expires_at,
            "profileVersion": self.profile_version,
        }


@dataclass(frozen=True)
class ProfileExclusion:
    source: str
    evidence_ref: str | None
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "evidenceRef": self.evidence_ref,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class LearnerProfile:
    facts: tuple[ProfileFact, ...]
    excluded: tuple[ProfileExclusion, ...]
    profile_version: str = PROFILE_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "profileVersion": self.profile_version,
            "facts": [fact.as_dict() for fact in self.facts],
            "excluded": [item.as_dict() for item in self.excluded],
        }

    def context(self) -> dict[str, Any]:
        """Return only bounded facts safe for a shadow context comparison."""
        return {
            "profileVersion": self.profile_version,
            "facts": [fact.as_dict() for fact in self.facts],
        }


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if row.get(key) is not None:
            return row[key]
    return None


def _contains_forbidden_key(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = "".join(char for char in str(key).lower() if char.isalnum())
            if normalized in FORBIDDEN_INPUT_KEYS:
                return str(key)
            found = _contains_forbidden_key(nested)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _contains_forbidden_key(nested)
            if found:
                return found
    return None


def _append_fact(
    candidates: list[tuple[str, Mapping[str, Any], str]],
    *,
    kind: str,
    row: Mapping[str, Any],
    now: float,
    ttl: float,
    excluded: list[ProfileExclusion],
) -> None:
    source = _text(row.get("source") or kind)
    evidence_ref = _text(_first(row, "evidenceRef", "evidence_ref", "attemptId", "attempt_id", "mistakeId", "mistake_id"))
    if not evidence_ref:
        excluded.append(ProfileExclusion(source, None, "missing_evidence_ref"))
        return
    forbidden = _contains_forbidden_key(row)
    if forbidden:
        excluded.append(ProfileExclusion(source, evidence_ref, f"forbidden_input_field:{forbidden}"))
        return
    publication_id = _text(_first(row, "publicationId", "publication_id"))
    subject_id = _text(_first(row, "knowledgePointId", "knowledge_point_id", "questionId", "question_id"))
    if not publication_id or not subject_id:
        excluded.append(ProfileExclusion(source, evidence_ref, "missing_publication_or_subject_scope"))
        return
    observed_at = _number(_first(row, "observedAt", "observed_at", "createdAt", "created_at", "confirmedAt", "confirmed_at"))
    if observed_at is None:
        excluded.append(ProfileExclusion(source, evidence_ref, "missing_observed_at"))
        return
    explicit_expires_at = _number(_first(row, "expiresAt", "expires_at"))
    expires_at = explicit_expires_at if explicit_expires_at is not None else observed_at + ttl
    if expires_at <= now:
        excluded.append(ProfileExclusion(source, evidence_ref, "stale"))
        return
    value = dict(row.get("value") or {}) if isinstance(row.get("value"), Mapping) else {
        key: row[key]
        for key in ("score", "assessment", "errorReason", "hintLevel", "hintCount", "attemptCount", "status")
        if row.get(key) is not None
    }
    candidates.append((f"{kind}:{publication_id}:{subject_id}", {
        "kind": kind,
        "publicationId": publication_id,
        "subjectId": subject_id,
        "value": value,
        "evidenceRef": evidence_ref,
        "observedAt": observed_at,
        "expiresAt": expires_at,
    }, source))


def build_learner_profile(
    *,
    mastery: Iterable[Mapping[str, Any]] = (),
    confirmed_mistakes: Iterable[Mapping[str, Any]] = (),
    hint_dependencies: Iterable[Mapping[str, Any]] = (),
    recent_practice: Iterable[Mapping[str, Any]] = (),
    now: float,
    ttls: Mapping[str, float] | None = None,
) -> LearnerProfile:
    """Build a short-lived profile from deterministic, caller-supplied facts."""
    durations = {**DEFAULT_TTLS, **(ttls or {})}
    candidates: list[tuple[str, Mapping[str, Any], str]] = []
    excluded: list[ProfileExclusion] = []
    sources = (
        ("mastery", mastery),
        ("confirmed_mistake", confirmed_mistakes),
        ("hint_dependency", hint_dependencies),
        ("recent_practice", recent_practice),
    )
    for kind, rows in sources:
        if kind not in ALLOWED_KINDS:
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                excluded.append(ProfileExclusion(kind, None, "invalid_fact"))
                continue
            if kind == "confirmed_mistake" and not _text(_first(row, "errorReason", "error_reason")):
                excluded.append(ProfileExclusion(kind, _text(_first(row, "evidenceRef", "mistakeId", "mistake_id")) or None, "mistake_not_confirmed"))
                continue
            _append_fact(candidates, kind=kind, row=row, now=now, ttl=float(durations[kind]), excluded=excluded)

    grouped: dict[str, list[tuple[Mapping[str, Any], str]]] = defaultdict(list)
    for identity, row, source in candidates:
        grouped[identity].append((row, source))
    facts: list[ProfileFact] = []
    for identity, entries in grouped.items():
        values = {repr(dict(item[0].get("value") or {})) for item in entries}
        if len(values) > 1:
            for row, source in entries:
                excluded.append(ProfileExclusion(source, str(row["evidenceRef"]), f"conflict:{identity}"))
            continue
        row, _ = max(entries, key=lambda item: (float(item[0]["observedAt"]), str(item[0]["evidenceRef"])))
        facts.append(ProfileFact(
            kind=str(row["kind"]),
            publication_id=str(row["publicationId"]),
            subject_id=str(row["subjectId"]),
            value=dict(row.get("value") or {}),
            evidence_ref=str(row["evidenceRef"]),
            observed_at=float(row["observedAt"]),
            expires_at=float(row["expiresAt"]),
        ))
    facts.sort(key=lambda item: (item.kind, item.publication_id, item.subject_id, item.observed_at, item.evidence_ref))
    return LearnerProfile(tuple(facts), tuple(excluded))
