"""Append-only storage for AI pre-review verdicts on courseware pages and source cases.

AI verdicts are advisory evidence kept apart from teacher decisions: nothing here
approves a page, decides a source case, or counts toward the human gold standard.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    select,
)
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import Engine

from persistence.database import decode_json
from persistence.schema import json_document

ai_review_metadata = MetaData()

ai_reviews = Table(
    "ai_reviews",
    ai_review_metadata,
    Column("review_id", String(64), primary_key=True),
    Column("subject_kind", String(32), nullable=False),
    # courseware_page: "<coursewareId>:<pageId>"; source_case: the quality-review case ID.
    Column("subject_id", String(200), nullable=False),
    Column("subject_version", Integer, nullable=False),
    Column("input_hash", String(64), nullable=False),
    Column("verdict", String(16), nullable=False),
    Column("review_json", json_document, nullable=False),
    Column("job_id", String(64)),
    Column("created_at", Float, nullable=False),
    CheckConstraint("subject_kind IN ('courseware_page', 'source_case')", name="ck_ai_reviews_subject_kind"),
    CheckConstraint("verdict IN ('pass', 'concern', 'fail')", name="ck_ai_reviews_verdict"),
    CheckConstraint("subject_version > 0", name="ck_ai_reviews_subject_version"),
    # Identical input is never sent to a model twice.
    UniqueConstraint("subject_kind", "subject_id", "input_hash", name="uq_ai_reviews_input"),
    Index("ix_ai_reviews_subject", "subject_kind", "subject_id", "created_at"),
)


class AiReviewStore:
    def __init__(self, *, engine: Engine) -> None:
        self.engine = engine

    def save(self, *, subject_kind: str, subject_id: str, subject_version: int, input_hash: str,
             verdict: str, review: dict[str, Any], job_id: str | None, now: float | None = None) -> dict[str, Any]:
        """Insert once per input; a concurrent duplicate returns the stored verdict."""
        created_at = time.time() if now is None else now
        row = {
            "review_id": uuid.uuid4().hex, "subject_kind": subject_kind, "subject_id": subject_id,
            "subject_version": subject_version, "input_hash": input_hash, "verdict": verdict,
            "review_json": review, "job_id": job_id, "created_at": created_at,
        }
        with self.engine.begin() as connection:
            connection.execute(postgresql_insert(ai_reviews).values(**row).on_conflict_do_nothing(
                constraint="uq_ai_reviews_input"))
        stored = self.find(subject_kind, subject_id, input_hash)
        assert stored is not None
        return stored

    def find(self, subject_kind: str, subject_id: str, input_hash: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(ai_reviews).where(
                ai_reviews.c.subject_kind == subject_kind, ai_reviews.c.subject_id == subject_id,
                ai_reviews.c.input_hash == input_hash,
            )).mappings().first()
        return self._view(row) if row else None

    def latest(self, subject_kind: str, subject_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Most recent verdict per subject; staleness is decided by the caller."""
        if not subject_ids:
            return {}
        with self.engine.connect() as connection:
            rows = connection.execute(select(ai_reviews).where(
                ai_reviews.c.subject_kind == subject_kind, ai_reviews.c.subject_id.in_(subject_ids),
            ).order_by(ai_reviews.c.created_at)).mappings().all()
        return {row["subject_id"]: self._view(row) for row in rows}

    @staticmethod
    def _view(row: Any) -> dict[str, Any]:
        review = decode_json(row["review_json"])
        return {**review, "reviewId": row["review_id"], "subjectKind": row["subject_kind"],
                "subjectId": row["subject_id"], "subjectVersion": int(row["subject_version"]),
                "inputHash": row["input_hash"], "verdict": row["verdict"], "jobId": row["job_id"],
                "createdAt": float(row["created_at"])}
