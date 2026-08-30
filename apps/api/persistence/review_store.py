"""Persistence for fixed-interval review tasks and their one-shot attempts."""

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
    UniqueConstraint,
    create_engine,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

review_metadata = MetaData()
json_document = JSON().with_variant(JSONB(), "postgresql")

review_tasks = Table(
    "review_tasks",
    review_metadata,
    Column("task_id", String(64), primary_key=True),
    Column("mistake_id", String(64), nullable=False),
    Column("learner_id", String(128), nullable=False),
    Column("interval_days", Integer, nullable=False),
    Column("due_at", Float, nullable=False),
    Column("status", String(32), nullable=False, default="scheduled"),
    Column("question_payload_json", json_document),
    Column("model_run_json", json_document, nullable=False, default=dict),
    Column("response_json", json_document, nullable=False, default=dict),
    Column("evaluation_evidence_json", json_document, nullable=False, default=dict),
    Column("assessment", String(32)),
    Column("feedback", Text, nullable=False, default=""),
    Column("created_at", Float, nullable=False),
    Column("started_at", Float),
    Column("completed_at", Float),
    UniqueConstraint("mistake_id", "interval_days", name="uq_review_task_interval"),
)

Index("idx_review_tasks_learner_due", review_tasks.c.learner_id, review_tasks.c.due_at)


class ReviewStore:
    """Store a deterministic 1/3/7-day schedule and auditable attempts."""

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
            review_metadata.create_all(self.engine)
            self._initialized = True

    def schedule(
        self,
        *,
        mistake_id: str,
        learner_id: str,
        base_time: float | None = None,
        intervals: tuple[int, ...] = (1, 3, 7),
    ) -> list[dict[str, Any]]:
        """Create missing intervals only, so retries never duplicate tasks."""
        self._ensure_initialized()
        anchor = base_time or time.time()
        existing = {item["intervalDays"] for item in self.list_for_mistake(mistake_id)}
        now = time.time()
        with self.engine.begin() as connection:
            for interval in intervals:
                if interval in existing:
                    continue
                connection.execute(review_tasks.insert().values(
                    task_id=uuid.uuid4().hex,
                    mistake_id=mistake_id,
                    learner_id=learner_id,
                    interval_days=interval,
                    due_at=anchor + interval * 86_400,
                    status="scheduled",
                    question_payload_json=None,
                    model_run_json={},
                    response_json={},
                    assessment=None,
                    feedback="",
                    created_at=now,
                    started_at=None,
                    completed_at=None,
                ))
        return self.list_for_mistake(mistake_id)

    def get(self, task_id: str) -> dict[str, Any] | None:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(review_tasks).where(review_tasks.c.task_id == task_id)
            ).mappings().first()
        return self._serialize(row) if row else None

    def list_for_learner(self, learner_id: str) -> list[dict[str, Any]]:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(review_tasks)
                .where(review_tasks.c.learner_id == learner_id)
                .order_by(review_tasks.c.due_at)
            ).mappings().all()
        return [self._serialize(row) for row in rows]

    def list_for_mistake(self, mistake_id: str) -> list[dict[str, Any]]:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(review_tasks)
                .where(review_tasks.c.mistake_id == mistake_id)
                .order_by(review_tasks.c.interval_days)
            ).mappings().all()
        return [self._serialize(row) for row in rows]

    def start(
        self,
        task_id: str,
        *,
        question_payload: dict[str, Any],
        model_run: dict[str, Any],
    ) -> dict[str, Any] | None:
        self._ensure_initialized()
        with self.engine.begin() as connection:
            result = connection.execute(
                review_tasks.update()
                .where(
                    review_tasks.c.task_id == task_id,
                    review_tasks.c.status == "scheduled",
                )
                .values(
                    status="ready",
                    question_payload_json=question_payload,
                    model_run_json=model_run,
                    started_at=time.time(),
                )
            )
        return self.get(task_id) if result.rowcount else None

    def answer(
        self,
        task_id: str,
        *,
        response: dict[str, Any],
        assessment: str,
        feedback: str,
        evaluation_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        self._ensure_initialized()
        evidence = evaluation_evidence or {}
        with self.engine.begin() as connection:
            result = connection.execute(
                review_tasks.update()
                .where(
                    review_tasks.c.task_id == task_id,
                    review_tasks.c.status == "ready",
                )
                .values(
                    status="completed",
                    response_json=response,
                    evaluation_evidence_json=evidence,
                    assessment=assessment,
                    feedback=feedback,
                    completed_at=time.time(),
                )
            )
        return self.get(task_id) if result.rowcount else None

    @staticmethod
    def _serialize(row: Any) -> dict[str, Any]:
        return {
            "taskId": row["task_id"],
            "mistakeId": row["mistake_id"],
            "learnerId": row["learner_id"],
            "intervalDays": row["interval_days"],
            "dueAt": row["due_at"],
            "status": row["status"],
            "questionPayload": row["question_payload_json"],
            "modelRun": row["model_run_json"] or {},
            "response": row["response_json"] or {},
            "evaluationEvidence": row["evaluation_evidence_json"] or {},
            "assessment": row["assessment"],
            "feedback": row["feedback"],
            "createdAt": row["created_at"],
            "startedAt": row["started_at"],
            "completedAt": row["completed_at"],
        }

    def close(self) -> None:
        self.engine.dispose()
