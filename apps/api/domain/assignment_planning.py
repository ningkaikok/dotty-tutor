"""Deterministic, privacy-preserving assignment planning rules.

The planner may ask a model to phrase or order goals, but these functions own
the facts.  In particular, mastery rows are grouped by a temporary normalized
topic key rather than by ``knowledge_point_id`` because that ID is scoped to a
publication.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, Iterable, Mapping

from domain.learning.mastery import normalize_knowledge_point_name

PLANNER_VERSION = "assignment-planner-v1"
PROMPT_VERSION = "assignment-planning-v1"
SCHEMA_VERSION = "assignment-plan-schema-v1"
VALID_ERROR_REASONS = {"concept", "reading", "calculation", "missing_step", "careless"}


def planning_topic_key(name: str | None) -> str:
    """Return the cross-publication key used only for planning aggregation."""
    return normalize_knowledge_point_name(name or "未分类知识点")


def _member_ids(members: Iterable[Any]) -> set[str]:
    return {
        str(item if isinstance(item, str) else item.get("learnerId") or item.get("learner_id"))
        for item in members
        if str(item if isinstance(item, str) else item.get("learnerId") or item.get("learner_id")) not in {"", "None"}
    }


def _topic_name(row: Mapping[str, Any]) -> str:
    return str(row.get("normalizedName") or row.get("normalized_name") or row.get("knowledgePoint") or row.get("knowledge_point") or "未分类知识点")


def aggregate_class_mastery(members: Iterable[Any], states: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate evidence without treating unobserved learners as zero.

    Multiple publication-scoped rows for one learner/topic are first averaged,
    so a learner who saw the same topic in two papers is not counted twice.
    """
    member_ids = _member_ids(members)
    per_learner: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    labels: dict[str, str] = {}
    for row in states:
        learner_id = str(row.get("learnerId") or row.get("learner_id") or "")
        evidence_value: Any = row.get("evidenceCount")
        if evidence_value is None:
            evidence_value = row.get("evidence_count")
        evidence_count = int(evidence_value) if evidence_value is not None else 0
        if learner_id not in member_ids or evidence_count <= 0:
            continue
        key = planning_topic_key(_topic_name(row))
        labels.setdefault(key, str(row.get("name") or row.get("knowledgePoint") or key))
        try:
            score = float(row.get("score", 0))
        except (TypeError, ValueError):
            continue
        per_learner[key][learner_id].append(max(0.0, min(1.0, score)))

    result: dict[str, dict[str, Any]] = {}
    for key, learners in per_learner.items():
        scores = [sum(values) / len(values) for values in learners.values()]
        distribution = {"needsSupport": 0, "developing": 0, "mastered": 0}
        for score in scores:
            if score >= 0.7:
                distribution["mastered"] += 1
            elif score >= 0.4:
                distribution["developing"] += 1
            else:
                distribution["needsSupport"] += 1
        result[key] = {
            "planningTopicKey": key,
            "topic": labels.get(key, key),
            "observedStudentCount": len(scores),
            "notObservedStudentCount": len(member_ids) - len(scores),
            "notObserved": len(member_ids) - len(scores),
            "averageScore": round(sum(scores) / len(scores), 4) if scores else None,
            "distribution": distribution,
            "evidenceCount": sum(len(values) for values in learners.values()),
        }
    return result


def aggregate_error_reason_stats(members: Iterable[Any], mistakes: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return self-reported, gated AI, and effective error statistics."""
    member_ids = _member_ids(members)
    result: dict[str, dict[str, Any]] = {}
    for row in mistakes:
        if str(row.get("learnerId") or row.get("learner_id") or "") not in member_ids:
            continue
        if row.get("status") not in {None, "unmastered", "mastered"}:
            continue
        key = planning_topic_key(str(row.get("knowledgePoint") or row.get("knowledge_point") or "未分类知识点"))
        item = result.setdefault(key, {
            "planningTopicKey": key,
            "selfReported": defaultdict(int),
            "aiAttributed": defaultdict(int),
            "effective": defaultdict(int),
            "total": 0,
        })
        self_reason = row.get("errorReason") or row.get("error_reason")
        ai_reason = row.get("aiErrorReason") or row.get("ai_error_reason")
        effective = ai_reason if ai_reason in VALID_ERROR_REASONS else self_reason if self_reason in VALID_ERROR_REASONS else None
        if self_reason in VALID_ERROR_REASONS:
            item["selfReported"][self_reason] += 1
        if ai_reason in VALID_ERROR_REASONS:
            item["aiAttributed"][ai_reason] += 1
        if effective:
            item["effective"][effective] += 1
        item["total"] += 1
    return {
        key: {
            **value,
            "selfReported": dict(value["selfReported"]),
            "aiAttributed": dict(value["aiAttributed"]),
            "effective": dict(value["effective"]),
        }
        for key, value in result.items()
    }


def build_publication_coverage(lessons: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Summarize the immutable question set by normalized topic."""
    result: dict[str, dict[str, Any]] = {}
    for lesson in lessons:
        question = ((lesson.get("questionPayload") or lesson.get("question_payload") or {}).get("question") or {})
        name = question.get("knowledgePoint") or (lesson.get("knowledgePoints") or [None])[0] or lesson.get("title") or "未分类知识点"
        key = planning_topic_key(str(name))
        item = result.setdefault(key, {"planningTopicKey": key, "topic": str(name), "questionCount": 0})
        item["questionCount"] += 1
    return result


def deterministic_goals(
    mastery: Mapping[str, Mapping[str, Any]],
    errors: Mapping[str, Mapping[str, Any]],
    coverage: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Rank supported, observed weaknesses with stable evidence references."""
    keys = set(mastery) | set(errors) | set(coverage)
    ranked: list[tuple[tuple[Any, ...], str]] = []
    for key in keys:
        mastery_item = mastery.get(key, {})
        error_item = errors.get(key, {})
        observed = int(mastery_item.get("observedStudentCount", 0))
        average = mastery_item.get("averageScore")
        effective_total = sum((error_item.get("effective") or {}).values())
        ranked.append((
            (0 if observed else 1, float(average) if average is not None else 1.0, -effective_total, key),
            key,
        ))
    goals: list[dict[str, Any]] = []
    for priority, (_, key) in enumerate(sorted(ranked), start=1):
        item = mastery.get(key, {})
        error_item = errors.get(key, {})
        topic = str(item.get("topic") or error_item.get("topic") or coverage.get(key, {}).get("topic") or key)
        average = item.get("averageScore")
        effective = error_item.get("effective") or {}
        reason = f"{topic}"
        if average is not None:
            reason += f"平均掌握度 {round(float(average) * 100)}%"
        if effective:
            reason += "；错因集中于" + "、".join(sorted(effective))
        observed = int(item.get("observedStudentCount", 0))
        refs = [f"mastery:{key}"] if observed else []
        if effective:
            refs.extend([f"mistakes:{key}:effective", f"mistakes:{key}:selfReported", f"mistakes:{key}:aiAttributed"])
        if key in coverage:
            refs.append(f"coverage:{key}")
        goals.append({
            "planningTopicKey": key,
            "topic": topic,
            "priority": priority,
            "objective": f"巩固{topic}，通过分步练习验证关键概念。",
            "reason": reason,
            "evidenceRefs": refs,
        })
    return goals


def source_fingerprint(snapshot: Mapping[str, Any]) -> str:
    """Hash the complete planning input using canonical JSON."""
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def planning_input_snapshot(*, class_size: int, publication: Mapping[str, Any], mastery: Mapping[str, Any], errors: Mapping[str, Any], coverage: Mapping[str, Any]) -> dict[str, Any]:
    """Build the persisted/model-safe snapshot; no learner identity or raw text."""
    return {
        "classSize": class_size,
        "publicationVersion": publication.get("version", 1),
        "publicationLessonCount": len(publication.get("lessonIds") or []),
        "mastery": mastery,
        "errors": errors,
        "coverage": coverage,
    }
