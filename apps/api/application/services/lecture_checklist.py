"""Deterministic teacher lecture checklist aggregation.

This service deliberately works from immutable attempt and review events.  It
does not change mastery or mistake records: a teacher's latest ``overturned``
event only changes the effective assessment shown in this projection.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any, Iterable, Mapping

from sqlalchemy import select

from persistence.database import decode_json
from persistence.mistake_store import mistake_items
from persistence.schema import (
    assignments,
    class_memberships,
    exercise_attempts,
    learning_classes,
    learning_sessions,
    lesson_documents,
    lesson_publications,
    teacher_review_events,
)

ERROR_REASONS = {"concept", "reading", "calculation", "missing_step", "unknown", "careless"}


def _latest(rows: Iterable[Mapping[str, Any]], key: Any) -> dict[Any, Mapping[str, Any]]:
    """Keep the last event for a key using its event time and stable id."""
    result: dict[Any, Mapping[str, Any]] = {}
    for row in rows:
        current = result.get(key(row))
        if current is None or (
            float(row.get("created_at") or 0), str(row.get("attempt_id") or row.get("event_id") or "")
        ) >= (
            float(current.get("created_at") or 0), str(current.get("attempt_id") or current.get("event_id") or "")
        ):
            result[key(row)] = row
    return result


def _question_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = decode_json(row.get("question_json")) or {}
    return (payload.get("question") or {}) if isinstance(payload, dict) else {}


def _attribution_for_mistake(row: Mapping[str, Any] | None) -> tuple[str, str, str | None]:
    """Return reason, source and the immutable mistake evidence reference."""
    if row:
        ai = row.get("ai_error_reason")
        self_reported = row.get("error_reason")
        if ai in ERROR_REASONS:
            return "ai", str(ai), str(row.get("mistake_id") or "") or None
        if self_reported in ERROR_REASONS:
            return "self", str(self_reported), str(row.get("mistake_id") or "") or None
    return "unknown", "unknown", None


def aggregate_lecture_checklist(
    *,
    members: Iterable[Mapping[str, Any]],
    questions: Iterable[Mapping[str, Any]],
    attempts: Iterable[Mapping[str, Any]],
    reviews: Iterable[Mapping[str, Any]],
    mistakes: Iterable[Mapping[str, Any]],
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Build common-mistake rows from plain records.

    This pure boundary makes the ordering and de-duplication rules testable
    without coupling the tests to SQLAlchemy.  The denominator for
    ``errorRate`` is students with a latest attempt for that question, so an
    unstarted student is never treated as a wrong answer.
    """
    member_by_id = {
        str(row.get("learnerId") or row.get("learner_id")): row
        for row in members
        if row.get("learnerId") or row.get("learner_id")
    }
    question_rows = list(questions)
    question_order = {
        str(row.get("questionId") or row.get("question_id")): index
        for index, row in enumerate(question_rows)
    }
    question_by_id = {
        str(row.get("questionId") or row.get("question_id")): row for row in question_rows
    }
    latest_attempts = _latest(
        (row for row in attempts if str(row.get("learnerId") or row.get("learner_id")) in member_by_id),
        lambda row: (str(row.get("learnerId") or row.get("learner_id")), str(row.get("questionId") or row.get("question_id"))),
    )
    latest_reviews = _latest(
        reviews,
        lambda row: (str(row.get("learnerId") or row.get("learner_id")), str(row.get("questionId") or row.get("question_id"))),
    )
    mistake_by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in mistakes:
        learner_id = str(row.get("learnerId") or row.get("learner_id") or "")
        payload = decode_json(row.get("question_payload_json") or row.get("questionPayload")) or {}
        question = payload.get("question") if isinstance(payload, dict) else {}
        question_id = str((question or {}).get("id") or row.get("questionId") or "")
        if learner_id in member_by_id and question_id:
            current = mistake_by_key.get((learner_id, question_id))
            if current is None or float(row.get("updated_at") or row.get("updatedAt") or 0) >= float(current.get("updated_at") or current.get("updatedAt") or 0):
                mistake_by_key[(learner_id, question_id)] = row

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (learner_id, question_id), attempt in latest_attempts.items():
        review = latest_reviews.get((learner_id, question_id))
        raw_assessment = str(attempt.get("assessment") or "unknown")
        effective_assessment = raw_assessment
        if review and review.get("action") == "overturned" and review.get("corrected_assessment"):
            effective_assessment = str(review["corrected_assessment"])
        if effective_assessment == "correct":
            continue
        member = member_by_id[learner_id]
        attribution_source, error_reason, mistake_id = _attribution_for_mistake(
            mistake_by_key.get((learner_id, question_id))
        )
        grouped[question_id].append({
            "learnerId": learner_id,
            "displayName": member.get("displayName") or member.get("display_name") or learner_id,
            "assessment": effective_assessment,
            "originalAssessment": raw_assessment,
            "reviewStatus": review.get("action") if review else "unreviewed",
            "correctedAssessment": review.get("corrected_assessment") if review and review.get("action") == "overturned" else None,
            "attributionSource": attribution_source,
            "errorReason": error_reason,
            "mistakeEvidenceRef": f"mistake:{mistake_id}:{learner_id}" if mistake_id else None,
            "evidenceRefs": [
                f"attempt:{attempt.get('attempt_id') or attempt.get('attemptId') or question_id}:{learner_id}",
                *([f"review:{review.get('event_id') or review.get('eventId')}:{learner_id}"] if review else []),
                *([f"mistake:{mistake_id}:{learner_id}"] if mistake_id else []),
            ],
        })

    items: list[dict[str, Any]] = []
    for question_id, wrong_students in grouped.items():
        question = question_by_id.get(question_id, {})
        attempted_count = sum(
            1 for (learner_id, current_question_id) in latest_attempts if current_question_id == question_id and learner_id in member_by_id
        )
        reason_counts: dict[str, int] = defaultdict(int)
        for student in wrong_students:
            reason_counts[student["errorReason"]] += 1
        reason_distribution = [
            {"reason": reason, "count": count, "rate": round(count / len(wrong_students), 4) if wrong_students else 0}
            for reason, count in sorted(reason_counts.items())
        ]
        refs = [ref for student in wrong_students for ref in student["evidenceRefs"]]
        items.append({
            "questionId": question_id,
            "questionOrder": question_order.get(question_id, len(question_order)),
            "title": question.get("title") or question.get("prompt") or question_id,
            "prompt": question.get("prompt") or "",
            "questionText": question.get("prompt") or question.get("title") or question_id,
            "involvedStudentCount": len(wrong_students),
            "studentCount": len(wrong_students),
            "attemptedStudentCount": attempted_count,
            "errorRate": round(len(wrong_students) / attempted_count, 4) if attempted_count else 0,
            "students": wrong_students,
            "involvedStudents": wrong_students,
            "errorReasons": reason_distribution,
            "errorReasonDistribution": reason_distribution,
            "evidenceRefs": refs,
        })
    items.sort(key=lambda item: (-item["involvedStudentCount"], -item["errorRate"], item["questionOrder"], item["questionId"]))
    return items[: min(50, max(1, int(limit)))]


class LectureChecklistService:
    """Read the assignment-scoped data needed by the teacher checklist."""

    def __init__(self, *, store: Any) -> None:
        self.store = store

    def get_checklist(self, *, class_id: str, assignment_id: str, limit: int = 5) -> dict[str, Any]:
        self.store._ensure_initialized()
        with self.store.engine.connect() as connection:
            class_row = connection.execute(select(learning_classes).where(learning_classes.c.class_id == class_id)).mappings().first()
            if not class_row:
                raise LookupError("班级不存在")
            members = connection.execute(
                select(class_memberships).where(class_memberships.c.class_id == class_id)
            ).mappings().all()
            assignment = connection.execute(
                select(assignments, lesson_publications.c.lesson_ids_json)
                .select_from(assignments.join(lesson_publications, assignments.c.publication_id == lesson_publications.c.publication_id))
                .where(assignments.c.assignment_id == assignment_id, assignments.c.class_id == class_id)
            ).mappings().first()
            if not assignment:
                raise LookupError("作业不存在或不属于该班级")
            lesson_ids = decode_json(assignment["lesson_ids_json"]) or []
            lessons = connection.execute(
                select(lesson_documents.c.lesson_id, lesson_documents.c.question_json, lesson_documents.c.title)
                .where(lesson_documents.c.lesson_id.in_(lesson_ids or ["__none__"]))
            ).mappings().all()
            member_ids = [row["learner_id"] for row in members]
            sessions = connection.execute(
                select(learning_sessions).where(
                    learning_sessions.c.assignment_id == assignment_id,
                    learning_sessions.c.learner_id.in_(member_ids or ["__none__"]),
                )
            ).mappings().all()
            session_ids = [row["session_id"] for row in sessions]
            attempts = connection.execute(
                select(exercise_attempts).where(exercise_attempts.c.session_id.in_(session_ids or ["__none__"]))
            ).mappings().all()
            reviews = connection.execute(
                select(teacher_review_events).where(
                    teacher_review_events.c.assignment_id == assignment_id,
                    teacher_review_events.c.learner_id.in_(member_ids or ["__none__"]),
                    teacher_review_events.c.question_id.is_not(None),
                )
            ).mappings().all()
            mistakes = connection.execute(
                select(mistake_items).where(mistake_items.c.learner_id.in_(member_ids or ["__none__"]))
            ).mappings().all()
        learner_by_session = {row["session_id"]: row["learner_id"] for row in sessions}
        questions = []
        lesson_by_id = {row["lesson_id"]: row for row in lessons}
        for index, lesson_id in enumerate(lesson_ids):
            lesson = lesson_by_id.get(lesson_id)
            if not lesson:
                continue
            question = _question_from_row(lesson)
            questions.append({
                "questionId": str(question.get("id") or lesson_id),
                "questionOrder": index,
                "title": lesson.get("title") or question.get("title"),
                "prompt": question.get("prompt") or "",
            })
        attempt_records = [{**row, "learner_id": learner_by_session.get(row["session_id"])} for row in attempts]
        # Published-attempt mistakes use this stable identity.  Filtering it
        # here prevents an identically named question from another publication
        # leaking into this assignment's error-reason distribution.
        question_ids = {str(item["questionId"]) for item in questions}
        expected_mistake_ids = {
            "paper-" + hashlib.sha256(f"{learner_id}:{assignment['publication_id']}:{question_id}".encode("utf-8")).hexdigest()[:32]
            for learner_id in member_ids
            for question_id in question_ids
        }
        assignment_mistakes = [row for row in mistakes if row.get("mistake_id") in expected_mistake_ids]
        items = aggregate_lecture_checklist(
            members=members,
            questions=questions,
            attempts=attempt_records,
            reviews=reviews,
            mistakes=assignment_mistakes,
            limit=limit,
        )
        reason_counts: dict[str, int] = defaultdict(int)
        for item in items:
            for reason in item["errorReasons"]:
                reason_counts[reason["reason"]] += reason["count"]
        reason_distribution = [
            {"reason": reason, "count": count}
            for reason, count in sorted(reason_counts.items())
        ]
        return {
            "classId": class_id,
            "assignmentId": assignment_id,
            "limit": min(50, max(1, int(limit))),
            "commonMistakes": items,
            "items": items,
            "errorReasonDistribution": reason_distribution,
            "evidenceRefs": [ref for item in items for ref in item["evidenceRefs"]],
        }
