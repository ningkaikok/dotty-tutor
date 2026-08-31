"""Persistence for generated variation exercises and their assessments."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
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
    func,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
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
    Column("attribution_source", String(16), nullable=False, default="unknown"),
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
    CheckConstraint(
        "attribution_source IN ('ai', 'self', 'unknown')",
        name="ck_variation_exercises_attribution_source",
    ),
)

# A variation row is the current projection used by the practice UI.  Every
# submission is also appended here so a correction never erases the original
# wrong answer or its deterministic EvaluationEvidence.
variation_attempts = Table(
    "variation_attempts",
    variation_metadata,
    Column("attempt_id", String(64), primary_key=True),
    Column("variation_id", String(64), nullable=False),
    Column("mistake_id", String(64), nullable=False),
    Column("learner_id", String(128), nullable=False),
    Column("attempt_number", Integer, nullable=False),
    Column("response_json", json_document, nullable=False),
    Column("evaluation_evidence_json", json_document, nullable=False, default=dict),
    Column("assessment", String(32), nullable=False),
    Column("feedback", Text, nullable=False, default=""),
    Column("created_at", Float, nullable=False),
)

Index(
    "idx_variation_exercises_mistake",
    variation_exercises.c.mistake_id,
    variation_exercises.c.sequence,
    unique=True,
)
Index(
    "idx_variation_attempts_variation",
    variation_attempts.c.variation_id,
    variation_attempts.c.attempt_number,
    unique=True,
)
Index(
    "idx_variation_attempts_learner",
    variation_attempts.c.learner_id,
    variation_attempts.c.created_at.desc(),
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
        # Serialize local submissions so the max(attempt_number) allocation
        # cannot race between concurrent requests.  PostgreSQL also locks the
        # variation projection below, covering separate worker transactions.
        self._answer_lock = threading.Lock()

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            from persistence.schema_registry import initialize_sqlite_schema

            if self.engine.dialect.name == "sqlite":
                initialize_sqlite_schema(self.engine)
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
        attribution_source: str = "unknown",
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
                attribution_source=attribution_source if attribution_source in {"ai", "self", "unknown"} else "unknown",
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
            item for variation in self.list_for_mistake(mistake_id)
            for item in self.list_attempts(variation["variationId"])
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

    def list_attempts(self, variation_id: str) -> list[dict[str, Any]]:
        """Return immutable submissions in the order they were accepted."""
        self._ensure_initialized()
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(variation_attempts)
                .where(variation_attempts.c.variation_id == variation_id)
                .order_by(variation_attempts.c.attempt_number)
            ).mappings().all()
        return [self._serialize_attempt(row) for row in rows]

    def get_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(variation_attempts).where(
                    variation_attempts.c.attempt_id == attempt_id
                )
            ).mappings().first()
        return self._serialize_attempt(row) if row else None

    def answer(
        self,
        variation_id: str,
        *,
        attempt_id: str | None = None,
        response: dict[str, Any],
        assessment: str,
        feedback: str,
        evaluation_evidence: dict[str, Any] | None = None,
        created_at: float | None = None,
    ) -> dict[str, Any] | None:
        """Append an answer and update the variation's current projection.

        ``attempt_id`` is the idempotency key.  The append-only row is the source
        of truth for learning evidence; the variation row remains a convenient
        latest-state projection for existing clients.  A correct projection is
        terminal, while an incorrect projection remains retryable.
        """
        self._ensure_initialized()
        stable_attempt_id = attempt_id or uuid.uuid4().hex
        timestamp = created_at or time.time()
        evidence = evaluation_evidence or {}
        with self._answer_lock, self.engine.begin() as connection:
            current = connection.execute(
                select(variation_exercises)
                .where(variation_exercises.c.variation_id == variation_id)
                .with_for_update()
            ).mappings().first()
            if not current:
                return None
            existing_attempt = connection.execute(
                select(variation_attempts).where(
                    variation_attempts.c.attempt_id == stable_attempt_id
                )
            ).mappings().first()
            if existing_attempt:
                if existing_attempt["variation_id"] != variation_id:
                    return None
                saved = self._serialize(current)
                saved["attemptId"] = existing_attempt["attempt_id"]
                saved["evaluationEvidence"] = existing_attempt["evaluation_evidence_json"] or {}
                return saved
            if current["status"] == "answered" and current["assessment"] == "correct":
                return None
            next_number = connection.execute(
                select(func.coalesce(func.max(variation_attempts.c.attempt_number), 0) + 1)
                .where(variation_attempts.c.variation_id == variation_id)
            ).scalar_one()
            values = {
                "attempt_id": stable_attempt_id,
                "variation_id": variation_id,
                "mistake_id": current["mistake_id"],
                "learner_id": current["learner_id"],
                "attempt_number": int(next_number),
                "response_json": response,
                "evaluation_evidence_json": evidence,
                "assessment": assessment,
                "feedback": feedback,
                "created_at": timestamp,
            }
            insert = (
                postgresql_insert(variation_attempts)
                if self.engine.dialect.name == "postgresql"
                else sqlite_insert(variation_attempts)
            )
            result = connection.execute(insert.values(**values).on_conflict_do_nothing())
            if not result.rowcount:
                return self._serialize(current)
            connection.execute(
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
                    answered_at=timestamp,
                )
            )
        saved = self.get(variation_id)
        if saved is not None:
            saved["attemptId"] = stable_attempt_id
            saved["evaluationEvidence"] = evidence
        return saved

    @staticmethod
    def _serialize(row: Any) -> dict[str, Any]:
        return {
            "variationId": row["variation_id"],
            "mistakeId": row["mistake_id"],
            "learnerId": row["learner_id"],
            "strategy": row["strategy"],
            "attributionSource": row.get("attribution_source") or "unknown",
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

    @staticmethod
    def _serialize_attempt(row: Any) -> dict[str, Any]:
        return {
            "attemptId": row["attempt_id"],
            "variationId": row["variation_id"],
            "mistakeId": row["mistake_id"],
            "learnerId": row["learner_id"],
            "attemptNumber": row["attempt_number"],
            "response": row["response_json"] or {},
            "evaluationEvidence": row["evaluation_evidence_json"] or {},
            "assessment": row["assessment"],
            "feedback": row["feedback"],
            "createdAt": row["created_at"],
        }

    def close(self) -> None:
        self.engine.dispose()
