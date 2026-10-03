"""PostgreSQL records for source-versioned chapter courses and English evidence."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from persistence.base import DatabaseStore
from persistence.database import decode_json
from persistence.schema import (
    chapter_english_attempt_reviews,
    chapter_english_attempts,
    chapter_records,
)


class ChapterStore(DatabaseStore):
    """Persist mutable studio state while published snapshots use lesson_publications."""

    @contextmanager
    def chapter_lock(self, chapter_id: str):
        """Serialize multi-row chapter workflows across API processes."""
        connection = self.engine.connect()
        try:
            connection.exec_driver_sql("SELECT pg_advisory_lock(hashtext(%s))", (chapter_id,))
            yield
        finally:
            try:
                connection.exec_driver_sql("SELECT pg_advisory_unlock(hashtext(%s))", (chapter_id,))
            finally:
                connection.close()

    def create_chapter(self, chapter: dict[str, Any]) -> dict[str, Any]:
        self._ensure_initialized()
        now = time.time()
        with self.atomic() as connection:
            connection.execute(chapter_records.insert().values(
                chapter_id=chapter["chapterId"], subject=chapter["subject"],
                status=chapter["status"], chapter_json=chapter,
                record_version=chapter.get("recordVersion", 1), created_at=now, updated_at=now,
            ))
        return chapter

    def load_chapter(self, chapter_id: str) -> dict[str, Any] | None:
        self._ensure_initialized()
        with self.read_connection() as connection:
            row = connection.execute(
                select(chapter_records.c.chapter_json, chapter_records.c.record_version).where(chapter_records.c.chapter_id == chapter_id)
            ).mappings().first()
        if row is None:
            return None
        chapter = decode_json(row["chapter_json"])
        chapter["recordVersion"] = row["record_version"]
        return chapter

    def list_chapters(self) -> list[dict[str, Any]]:
        self._ensure_initialized()
        with self.read_connection() as connection:
            rows = connection.execute(select(chapter_records.c.chapter_json).order_by(
                chapter_records.c.updated_at.desc()
            )).scalars().all()
        return [decode_json(row) for row in rows]

    def save_chapter(self, chapter: dict[str, Any]) -> dict[str, Any]:
        self._ensure_initialized()
        with self.atomic() as connection:
            row = connection.execute(
                select(chapter_records.c.record_version).where(
                    chapter_records.c.chapter_id == chapter["chapterId"]
                ).with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise LookupError("章节不存在")
            if row != chapter.get("recordVersion", 1):
                raise ValueError("章节已被其他编辑更新，请刷新后重试")
            chapter["recordVersion"] = row + 1
            connection.execute(chapter_records.update().where(
                chapter_records.c.chapter_id == chapter["chapterId"],
                chapter_records.c.record_version == row,
            ).values(
                subject=chapter["subject"], status=chapter["status"],
                chapter_json=chapter, record_version=row + 1, updated_at=time.time(),
            ))
        return chapter

    def get_chapter_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        self._ensure_initialized()
        with self.read_connection() as connection:
            row = connection.execute(select(chapter_english_attempts).where(
                chapter_english_attempts.c.attempt_id == attempt_id
            )).mappings().first()
            reviews = connection.execute(select(chapter_english_attempt_reviews).where(
                chapter_english_attempt_reviews.c.attempt_id == attempt_id
            ).order_by(chapter_english_attempt_reviews.c.created_at)).mappings().all()
        if not row:
            return None
        return {
            "attemptId": row["attempt_id"], "subject": row["subject"], "publicationId": row["publication_id"],
            "learnerId": row["learner_id"], "lessonId": row["lesson_id"],
            "answer": decode_json(row["answer_json"]), "evidenceRefs": decode_json(row["evidence_json"]),
            "assessment": row["assessment"], "evidenceVerdict": row["evidence_verdict"],
            "feedback": decode_json(row["feedback_json"]),
            "reviews": [{"reviewer": item["reviewer"], "decision": item["decision"], "note": item["note"], "createdAt": item["created_at"]} for item in reviews],
        }

    def get_chapter_english_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        attempt = self.get_chapter_attempt(attempt_id)
        return attempt if attempt and attempt.get("subject") == "english" else None

    def review_chapter_english_attempt(self, attempt_id: str, reviewer: str, decision: str, note: str) -> dict[str, Any]:
        self._ensure_initialized()
        import uuid
        with self.atomic() as connection:
            exists = connection.execute(select(chapter_english_attempts.c.attempt_id).where(
                chapter_english_attempts.c.attempt_id == attempt_id
            ).with_for_update()).scalar_one_or_none()
            if not exists:
                raise LookupError("英语作答记录不存在")
            connection.execute(chapter_english_attempt_reviews.insert().values(
                review_id=uuid.uuid4().hex, attempt_id=attempt_id, reviewer=reviewer,
                decision=decision, note=note, created_at=time.time(),
            ))
        return self.get_chapter_english_attempt(attempt_id) or {}

    def save_chapter_attempt(self, attempt: dict[str, Any]) -> dict[str, Any]:
        self._ensure_initialized()
        with self.atomic() as connection:
            statement = postgresql_insert(chapter_english_attempts).values(
                attempt_id=attempt["attemptId"], subject=attempt["subject"], publication_id=attempt["publicationId"],
                learner_id=attempt["learnerId"], lesson_id=attempt["lessonId"],
                answer_json=attempt["answer"], evidence_json=attempt["evidenceRefs"],
                assessment=attempt["assessment"], feedback_json=attempt["feedback"],
                evidence_verdict=attempt["evidenceVerdict"],
                created_at=time.time(),
            ).on_conflict_do_nothing(index_elements=[chapter_english_attempts.c.attempt_id])
            connection.execute(statement)
            saved = self.get_chapter_attempt(attempt["attemptId"])
            if not saved or any(saved.get(key) != attempt.get(key) for key in (
                "subject", "publicationId", "learnerId", "lessonId", "answer", "evidenceRefs",
                "assessment", "evidenceVerdict", "feedback",
            )):
                raise ValueError("attemptId 已用于不同作答")
            return saved

    def save_chapter_english_attempt(self, attempt: dict[str, Any]) -> dict[str, Any]:
        attempt.setdefault("subject", "english")
        return self.save_chapter_attempt(attempt)
