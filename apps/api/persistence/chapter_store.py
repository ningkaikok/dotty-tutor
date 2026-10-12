"""PostgreSQL records for source-versioned chapter courses and English evidence."""

from __future__ import annotations

import hashlib
import time
from contextlib import contextmanager
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from persistence.base import DatabaseStore
from persistence.database import decode_json
from persistence.schema import (
    background_jobs,
    chapter_english_attempt_reviews,
    chapter_english_attempts,
    chapter_records,
    upload_jobs,
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

    def create_chapter(
        self,
        chapter: dict[str, Any],
        *,
        execution_context: Any | None = None,
        source_guard: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Insert a source chapter only while its creating Worker still owns the lease.

        Source preparation is a durable job. The worker and upload rows are locked
        together so an expired attempt or concurrently replaced OCR snapshot cannot
        commit a chapter after the request has moved on.
        """
        self._ensure_initialized()
        now = time.time()
        with self.atomic() as connection:
            if execution_context is not None:
                job = connection.execute(select(background_jobs).where(
                    background_jobs.c.job_id == execution_context.job_id,
                    background_jobs.c.status == "running",
                    background_jobs.c.lease_owner == execution_context.worker_id,
                    background_jobs.c.attempt_count == execution_context.attempt_count,
                    background_jobs.c.lease_expires_at > now,
                    background_jobs.c.cancel_requested.is_(False),
                ).with_for_update()).mappings().first()
                if job is None:
                    raise ValueError("教材来源准备任务租约已过期或已被接管")
            if source_guard is not None:
                source_row = connection.execute(select(
                    upload_jobs.c.status, upload_jobs.c.source_text,
                ).where(upload_jobs.c.upload_id == source_guard["uploadId"]).with_for_update()).mappings().first()
                actual_hash = hashlib.sha256(str((source_row or {}).get("source_text") or "").encode()).hexdigest()
                if not source_row or source_row["status"] not in {"complete", "completed", "ready"}:
                    raise ValueError("教材来源在准备期间已不可用")
                if actual_hash != source_guard["sourceTextSha256"]:
                    raise ValueError("教材 OCR 来源在准备期间已变化")
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
