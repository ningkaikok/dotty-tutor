"""Persistence operations for lessons, attempts and learner mastery."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import func, select

from persistence.base import DatabaseStore
from persistence.database import decode_json
from persistence.schema import (
    batch_questions,
    exercise_attempts,
    learning_sessions,
    lesson_documents,
    mastery_states,
    upload_jobs,
)


class LearningStore(DatabaseStore):
    """Store programmable lessons and the learner's practice history."""

    def save_lesson(self, document: dict[str, Any]) -> dict[str, Any]:
        self._ensure_initialized()
        now = time.time()
        existing = self.load_lesson(document["lessonId"])
        values = {
            "lesson_id": document["lessonId"],
            "source_upload_id": document.get("sourceUploadId"),
            "title": document["title"],
            "version": document.get("version", 1),
            "status": document.get("status", "draft"),
            "knowledge_points_json": document.get("knowledgePoints", []),
            "blocks_json": document.get("blocks", []),
            "created_at": existing.get("createdAt", now) if existing else now,
            "updated_at": now,
        }
        with self.engine.begin() as connection:
            self._upsert(
                connection,
                lesson_documents,
                values,
                ["lesson_id"],
                [
                    "source_upload_id", "title", "version", "status",
                    "knowledge_points_json", "blocks_json", "updated_at",
                ],
            )
        return self.load_lesson(document["lessonId"]) or document

    def load_lesson(self, lesson_id: str) -> dict[str, Any] | None:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(lesson_documents).where(lesson_documents.c.lesson_id == lesson_id)
            ).mappings().first()
        if not row:
            return None
        return {
            "lessonId": row["lesson_id"],
            "sourceUploadId": row["source_upload_id"],
            "title": row["title"],
            "version": row["version"],
            "status": row["status"],
            "knowledgePoints": decode_json(row["knowledge_points_json"]),
            "blocks": decode_json(row["blocks_json"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def create_learning_session(
        self,
        *,
        session_id: str,
        learner_id: str,
        lesson_id: str,
        started_at: float,
    ) -> dict[str, Any]:
        self._ensure_initialized()
        with self.engine.begin() as connection:
            connection.execute(learning_sessions.insert().values(
                session_id=session_id,
                learner_id=learner_id,
                lesson_id=lesson_id,
                started_at=started_at,
                updated_at=started_at,
            ))
        return {
            "sessionId": session_id,
            "learnerId": learner_id,
            "lessonId": lesson_id,
            "startedAt": started_at,
        }

    def record_exercise_attempt(
        self,
        *,
        attempt_id: str,
        session_id: str,
        question_id: str,
        knowledge_point: str,
        response: dict[str, Any],
        assessment: str,
        hint_level: int,
        duration_ms: int,
        created_at: float,
    ) -> dict[str, Any]:
        self._ensure_initialized()
        target = {"correct": 1.0, "partial": 0.55, "incorrect": 0.0}[assessment]
        with self.engine.begin() as connection:
            session = connection.execute(
                select(learning_sessions).where(
                    learning_sessions.c.session_id == session_id
                )
            ).mappings().first()
            if not session:
                raise LookupError("学习会话不存在")
            connection.execute(exercise_attempts.insert().values(
                attempt_id=attempt_id,
                session_id=session_id,
                question_id=question_id,
                knowledge_point=knowledge_point,
                response_json=response,
                assessment=assessment,
                hint_level=hint_level,
                duration_ms=duration_ms,
                created_at=created_at,
            ))
            current = connection.execute(
                select(mastery_states).where(
                    mastery_states.c.learner_id == session["learner_id"],
                    mastery_states.c.knowledge_point == knowledge_point,
                )
            ).mappings().first()
            previous_score = float(current["score"]) if current else 0.0
            attempt_count = int(current["attempt_count"]) + 1 if current else 1
            previous_correct = int(current["correct_count"]) if current else 0
            correct_count = previous_correct + (1 if assessment == "correct" else 0)
            score = round(previous_score * 0.7 + target * 0.3, 4)
            self._upsert(
                connection,
                mastery_states,
                {
                    "learner_id": session["learner_id"],
                    "knowledge_point": knowledge_point,
                    "score": score,
                    "attempt_count": attempt_count,
                    "correct_count": correct_count,
                    "last_practiced_at": created_at,
                },
                ["learner_id", "knowledge_point"],
                ["score", "attempt_count", "correct_count", "last_practiced_at"],
            )
            connection.execute(
                learning_sessions.update()
                .where(learning_sessions.c.session_id == session_id)
                .values(updated_at=created_at)
            )
        return {
            "attemptId": attempt_id,
            "mastery": {
                "learnerId": session["learner_id"],
                "knowledgePoint": knowledge_point,
                "score": score,
                "attemptCount": attempt_count,
                "correctCount": correct_count,
                "lastPracticedAt": created_at,
            },
        }

    def list_mastery(self, learner_id: str) -> list[dict[str, Any]]:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(mastery_states)
                .where(mastery_states.c.learner_id == learner_id)
                .order_by(mastery_states.c.last_practiced_at.desc())
            ).mappings().all()
        return [{
            "learnerId": row["learner_id"],
            "knowledgePoint": row["knowledge_point"],
            "score": row["score"],
            "attemptCount": row["attempt_count"],
            "correctCount": row["correct_count"],
            "lastPracticedAt": row["last_practiced_at"],
        } for row in rows]

    def counts(self) -> dict[str, int]:
        """Return row counts for migration verification and diagnostics."""
        self._ensure_initialized()
        with self.engine.connect() as connection:
            return {
                "upload_jobs": connection.scalar(
                    select(func.count()).select_from(upload_jobs)
                ) or 0,
                "batch_questions": connection.scalar(
                    select(func.count()).select_from(batch_questions)
                ) or 0,
                "lesson_documents": connection.scalar(
                    select(func.count()).select_from(lesson_documents)
                ) or 0,
                "learning_sessions": connection.scalar(
                    select(func.count()).select_from(learning_sessions)
                ) or 0,
                "exercise_attempts": connection.scalar(
                    select(func.count()).select_from(exercise_attempts)
                ) or 0,
            }
