"""Persistence for fixed-interval review tasks and their one-shot attempts."""

from __future__ import annotations

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
from sqlalchemy.exc import IntegrityError

from domain.learning.mastery_policy import (
    LEGACY_POLICY_VERSION,
    resolve_policy,
)
from domain.learning.review_scheduler import follow_up_schedule, initial_schedule

review_metadata = MetaData()
json_document = JSON().with_variant(JSONB(), "postgresql")

review_tasks = Table(
    "review_tasks",
    review_metadata,
    Column("task_id", String(64), primary_key=True),
    Column("mistake_id", String(64), nullable=False),
    Column("learner_id", String(128), nullable=False),
    Column("interval_days", Integer, nullable=False),
    Column("schedule_version", String(64), nullable=False, default=LEGACY_POLICY_VERSION, server_default=LEGACY_POLICY_VERSION),
    Column("sequence_no", Integer, nullable=False, default=0, server_default="0"),
    Column("profile", String(64), nullable=False, default="unknown:legacy", server_default="unknown:legacy"),
    Column("trigger_evidence_ref", String(255)),
    Column("superseded_at", Float),
    Column("due_at", Float, nullable=False),
    Column("status", String(32), nullable=False, default="scheduled"),
    Column("question_payload_json", json_document),
    Column("model_run_json", json_document, nullable=False, default=dict),
    Column("response_json", json_document, nullable=False, default=dict),
    Column("evaluation_evidence_json", json_document, nullable=False, default=dict, server_default="{}"),
    Column("assessment", String(32)),
    Column("feedback", Text, nullable=False, default=""),
    Column("created_at", Float, nullable=False),
    Column("started_at", Float),
    Column("completed_at", Float),
    UniqueConstraint("mistake_id", "schedule_version", "sequence_no", name="uq_review_task_schedule_sequence"),
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

    def _ensure_initialized(self) -> None:
        """Keep store call sites uniform; schema creation is Alembic-owned."""
        return None

    def schedule(
        self,
        *,
        mistake_id: str,
        learner_id: str,
        base_time: float | None = None,
        intervals: tuple[int, ...] | None = None,
        objective_type: str | None = None,
        gate_mode: str | None = None,
        policy_version: str | None = None,
        trigger_evidence_ref: str | None = None,
    ) -> list[dict[str, Any]]:
        """Create an idempotent initial schedule for a policy profile.

        Omitting policy metadata resolves to the legacy profile, preserving the
        existing 1/3/7-day API and historical rows.
        """
        self._ensure_initialized()
        anchor = base_time or time.time()
        policy = resolve_policy(objective_type, gate_mode, policy_version)
        schedule_version = policy.policy_version
        profile = policy.profile
        planned = initial_schedule(
            policy,
            base_time=anchor,
            trigger_evidence_ref=trigger_evidence_ref,
        ) if intervals is None else [
            {
                "intervalDays": interval,
                "dueAt": anchor + interval * 86_400,
                "scheduleVersion": schedule_version,
                "sequenceNo": index + 1,
                "profile": profile,
                "triggerEvidenceRef": trigger_evidence_ref,
                "supersededAt": None,
            }
            for index, interval in enumerate(intervals)
        ]
        now = time.time()
        with self.engine.begin() as connection:
            for item in planned:
                values = {
                    "task_id": uuid.uuid4().hex,
                    "mistake_id": mistake_id,
                    "learner_id": learner_id,
                    "interval_days": item["intervalDays"],
                    "schedule_version": item["scheduleVersion"],
                    "sequence_no": item["sequenceNo"],
                    "profile": item["profile"],
                    "trigger_evidence_ref": item["triggerEvidenceRef"],
                    "superseded_at": item["supersededAt"],
                    "due_at": item["dueAt"],
                    "status": "scheduled",
                    "question_payload_json": None,
                    "model_run_json": {},
                    "response_json": {},
                    "assessment": None,
                    "feedback": "",
                    "created_at": now,
                    "started_at": None,
                    "completed_at": None,
                }
                try:
                    # A savepoint keeps the outer transaction usable after a
                    # concurrent caller wins the unique schedule slot.
                    with connection.begin_nested():
                        connection.execute(review_tasks.insert().values(**values))
                except IntegrityError:
                    winner = connection.execute(
                        select(review_tasks).where(
                            review_tasks.c.mistake_id == mistake_id,
                            review_tasks.c.schedule_version == item["scheduleVersion"],
                            review_tasks.c.sequence_no == item["sequenceNo"],
                        )
                    ).mappings().first()
                    if winner is None:
                        raise
        return self.list_for_mistake(mistake_id)

    def schedule_follow_up(
        self,
        task: dict[str, Any],
        *,
        assessment: str,
        next_action: str,
        trigger_evidence_ref: str | None = None,
        base_time: float | None = None,
    ) -> dict[str, Any] | None:
        """Schedule one next task and supersede all open retries when needed."""
        now = time.time()
        with self.engine.begin() as connection:
            return self._schedule_follow_up_in_connection(
                connection,
                task,
                assessment=assessment,
                next_action=next_action,
                trigger_evidence_ref=trigger_evidence_ref,
                base_time=base_time,
                now=now,
            )

    def _schedule_follow_up_in_connection(
        self,
        connection: Any,
        task: dict[str, Any],
        *,
        assessment: str,
        next_action: str,
        trigger_evidence_ref: str | None = None,
        base_time: float | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Schedule a follow-up using a caller-owned transaction.

        Keeping the collision handling and supersession in the same transaction
        as completion lets an answer either persist with its follow-up or roll
        back as a unit.  The unique schedule key still makes replay safe.
        """
        transaction_now = now if now is not None else time.time()
        profile = str(task.get("profile") or "unknown:legacy")
        objective_type, _, gate_mode = profile.partition(":")
        policy = resolve_policy(
            task.get("objectiveType") or objective_type,
            task.get("gateMode") or gate_mode,
            task.get("policyVersion") or task.get("scheduleVersion"),
        )
        planned = follow_up_schedule(
            policy,
            base_time=base_time if base_time is not None else transaction_now,
            sequence_no=int(task.get("sequenceNo") or 0),
            assessment=assessment,
            next_action=next_action,
            schedule_version=task.get("scheduleVersion"),
            trigger_evidence_ref=trigger_evidence_ref,
        )
        if not planned:
            return None
        if planned.get("supersedeFuture"):
            connection.execute(
                review_tasks.update()
                .where(
                    review_tasks.c.mistake_id == task["mistakeId"],
                    review_tasks.c.learner_id == task["learnerId"],
                    review_tasks.c.status.in_(("scheduled", "ready")),
                    review_tasks.c.task_id != task["taskId"],
                    review_tasks.c.superseded_at.is_(None),
                    ~(
                        (review_tasks.c.schedule_version == planned["scheduleVersion"])
                        & (review_tasks.c.sequence_no == planned["sequenceNo"])
                    ),
                )
                .values(status="superseded", superseded_at=transaction_now)
            )
        next_task_id = uuid.uuid4().hex
        try:
            with connection.begin_nested():
                connection.execute(review_tasks.insert().values(
                    task_id=next_task_id,
                    mistake_id=task["mistakeId"],
                    learner_id=task["learnerId"],
                    interval_days=planned["intervalDays"],
                    schedule_version=planned["scheduleVersion"],
                    sequence_no=planned["sequenceNo"],
                    profile=planned["profile"],
                    trigger_evidence_ref=planned["triggerEvidenceRef"],
                    superseded_at=None,
                    due_at=planned["dueAt"],
                    status="scheduled",
                    question_payload_json=None,
                    model_run_json={},
                    response_json={},
                    evaluation_evidence_json={},
                    assessment=None,
                    feedback="",
                    created_at=transaction_now,
                    started_at=None,
                    completed_at=None,
                ))
        except IntegrityError:
            winner = connection.execute(
                select(review_tasks).where(
                    review_tasks.c.mistake_id == task["mistakeId"],
                    review_tasks.c.learner_id == task["learnerId"],
                    review_tasks.c.schedule_version == planned["scheduleVersion"],
                    review_tasks.c.sequence_no == planned["sequenceNo"],
                )
            ).mappings().first()
            if winner is None:
                raise
            return self._serialize(winner)
        row = connection.execute(
            select(review_tasks).where(review_tasks.c.task_id == next_task_id)
        ).mappings().first()
        return self._serialize(row) if row else None

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
                .order_by(review_tasks.c.sequence_no, review_tasks.c.due_at, review_tasks.c.interval_days)
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

    def answer_and_schedule_follow_up(
        self,
        task_id: str,
        *,
        response: dict[str, Any],
        assessment: str,
        feedback: str,
        evaluation_evidence: dict[str, Any] | None = None,
        next_action: str,
        trigger_evidence_ref: str | None = None,
    ) -> dict[str, Any] | None:
        """Atomically complete an answer and ensure its follow-up exists.

        A completed replay is accepted only when its persisted answer and
        evidence exactly match.  This is the recovery path for a client that
        timed out after completion or for rows written by the old two-step
        implementation; it never overwrites an existing assessment.
        """
        self._ensure_initialized()
        evidence = evaluation_evidence or {}
        now = time.time()
        with self.engine.begin() as connection:
            row = connection.execute(
                select(review_tasks)
                .where(review_tasks.c.task_id == task_id)
                .with_for_update()
            ).mappings().first()
            if row is None:
                return None
            status = row["status"]
            if status == "ready":
                connection.execute(
                    review_tasks.update()
                    .where(review_tasks.c.task_id == task_id, review_tasks.c.status == "ready")
                    .values(
                        status="completed",
                        response_json=response,
                        evaluation_evidence_json=evidence,
                        assessment=assessment,
                        feedback=feedback,
                        completed_at=now,
                    )
                )
                row = connection.execute(
                    select(review_tasks).where(review_tasks.c.task_id == task_id)
                ).mappings().first()
            elif status == "completed":
                if (
                    (row["response_json"] or {}) != response
                    or (row["evaluation_evidence_json"] or {}) != evidence
                    or row["assessment"] != assessment
                ):
                    raise ValueError("已完成的复习答案或证据不一致，不能覆盖已有结果")
            else:
                return None
            if row is None:
                return None
            saved = self._serialize(row)
            self._schedule_follow_up_in_connection(
                connection,
                saved,
                assessment=assessment,
                next_action=next_action,
                trigger_evidence_ref=trigger_evidence_ref,
                base_time=saved["completedAt"],
                now=now,
            )
            return saved

    @staticmethod
    def _serialize(row: Any) -> dict[str, Any]:
        schedule_version = row.get("schedule_version") or LEGACY_POLICY_VERSION
        profile = row.get("profile") or "unknown:legacy"
        objective_type, _, gate_mode = str(profile).partition(":")
        return {
            "taskId": row["task_id"],
            "mistakeId": row["mistake_id"],
            "learnerId": row["learner_id"],
            "intervalDays": row["interval_days"],
            "scheduleVersion": schedule_version,
            "sequenceNo": int(row.get("sequence_no") or 0),
            "profile": profile,
            "objectiveType": objective_type or "unknown",
            "gateMode": gate_mode or "legacy",
            "policyVersion": schedule_version,
            "triggerEvidenceRef": row.get("trigger_evidence_ref"),
            "supersededAt": row.get("superseded_at"),
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
