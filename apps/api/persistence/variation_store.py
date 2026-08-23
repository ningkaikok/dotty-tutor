"""Persistence for generated variation exercises and their assessments."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    create_engine,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

variation_metadata = MetaData()
json_document = JSON().with_variant(JSONB(), "postgresql")

variation_exercises = Table(
    "variation_exercises",
    variation_metadata,
    Column("variation_id", String(64), primary_key=True),
    Column("mistake_id", String(64), nullable=False),
    Column("learner_id", String(128), nullable=False),
    Column("strategy", String(32), nullable=False),
    Column("level", String(32), nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("question_payload_json", json_document, nullable=False),
    Column("model_run_json", json_document, nullable=False, default=dict),
    Column("status", String(32), nullable=False, default="ready"),
    Column("assessment", String(32)),
    Column("response_json", json_document, nullable=False, default=dict),
    Column("feedback", Text, nullable=False, default=""),
    Column("created_at", Float, nullable=False),
    Column("answered_at", Float),
)

Index(
    "idx_variation_exercises_mistake",
    variation_exercises.c.mistake_id,
    variation_exercises.c.sequence,
    unique=True,
)
Index(
    "idx_variation_exercises_learner",
    variation_exercises.c.learner_id,
    variation_exercises.c.created_at.desc(),
)


class VariationStore:
    """Store one generated validation question and its latest answer evidence.

    A wrong answer is deliberately editable: the student should correct the same
    validation question instead of being forced to generate another model-backed
    question.  Only a correct answer closes the record.
    """

    def __init__(self, *, engine: Engine | None = None, database_url: str | None = None) -> None:
        if engine is None:
            if not database_url:
                raise ValueError("engine 或 database_url 必须提供一个")
            self.engine = create_engine(database_url, future=True)
        else:
            self.engine = engine
        self._initialized = False
        self._initialize_lock = threading.Lock()

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            variation_metadata.create_all(self.engine)
            self._initialized = True

    def create(
        self,
        *,
        mistake_id: str,
        learner_id: str,
        strategy: str,
        level: str,
        question_payload: dict[str, Any],
        model_run: dict[str, Any],
    ) -> dict[str, Any]:
        self._ensure_initialized()
        sequence = self.count_for_mistake(mistake_id) + 1
        now = time.time()
        variation_id = uuid.uuid4().hex
        with self.engine.begin() as connection:
            connection.execute(variation_exercises.insert().values(
                variation_id=variation_id,
                mistake_id=mistake_id,
                learner_id=learner_id,
                strategy=strategy,
                level=level,
                sequence=sequence,
                question_payload_json=question_payload,
                model_run_json=model_run,
                status="ready",
                assessment=None,
                response_json={},
                feedback="",
                created_at=now,
                answered_at=None,
            ))
        return self.get(variation_id) or {}

    def get(self, variation_id: str) -> dict[str, Any] | None:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(variation_exercises).where(
                    variation_exercises.c.variation_id == variation_id
                )
            ).mappings().first()
        return self._serialize(row) if row else None

    def list_for_mistake(self, mistake_id: str) -> list[dict[str, Any]]:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(variation_exercises)
                .where(variation_exercises.c.mistake_id == mistake_id)
                .order_by(variation_exercises.c.sequence)
            ).mappings().all()
        return [self._serialize(row) for row in rows]

    def count_for_mistake(self, mistake_id: str) -> int:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(variation_exercises.c.variation_id).where(
                    variation_exercises.c.mistake_id == mistake_id
                )
            ).all()
        return len(rows)

    def mastery_summary(self, mistake_id: str, *, required: int = 1) -> dict[str, Any]:
        """Derive the current streak from evidence instead of a mutable counter."""
        attempts = [
            item for item in self.list_for_mistake(mistake_id)
            if item["status"] == "answered"
        ]
        streak = 0
        for attempt in reversed(attempts):
            if attempt["assessment"] != "correct":
                break
            streak += 1
        return {
            "correctStreak": streak,
            "requiredCorrect": required,
            "mastered": streak >= required,
            "answeredCount": len(attempts),
        }

    def answer(
        self,
        variation_id: str,
        *,
        response: dict[str, Any],
        assessment: str,
        feedback: str,
    ) -> dict[str, Any] | None:
        """Save an answer, allowing correction after an incorrect submission.

        The SQL predicate is repeated here instead of trusting the earlier read,
        so two browser requests cannot overwrite a correctly completed question.
        A correct record is therefore terminal while an incorrect record remains
        retryable.
        """
        self._ensure_initialized()
        current = self.get(variation_id)
        if not current or (
            current["status"] == "answered" and current["assessment"] == "correct"
        ):
            return None
        with self.engine.begin() as connection:
            result = connection.execute(
                variation_exercises.update()
                .where(
                    variation_exercises.c.variation_id == variation_id,
                    or_(
                        variation_exercises.c.status == "ready",
                        and_(
                            variation_exercises.c.status == "answered",
                            variation_exercises.c.assessment != "correct",
                        ),
                    ),
                )
                .values(
                    status="answered",
                    assessment=assessment,
                    response_json=response,
                    feedback=feedback,
                    answered_at=time.time(),
                )
            )
        return self.get(variation_id) if result.rowcount else None

    @staticmethod
    def _serialize(row: Any) -> dict[str, Any]:
        return {
            "variationId": row["variation_id"],
            "mistakeId": row["mistake_id"],
            "learnerId": row["learner_id"],
            "strategy": row["strategy"],
            "level": row["level"],
            "sequence": row["sequence"],
            "questionPayload": row["question_payload_json"],
            "modelRun": row["model_run_json"] or {},
            "status": row["status"],
            "assessment": row["assessment"],
            "response": row["response_json"] or {},
            "feedback": row["feedback"],
            "createdAt": row["created_at"],
            "answeredAt": row["answered_at"],
        }

    def close(self) -> None:
        self.engine.dispose()
