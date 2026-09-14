"""PostgreSQL persistence for tutor threads and bounded messages."""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

from domain.constants import DEMO_LEARNER_ID

tutoring_metadata = MetaData()
json_document = JSON().with_variant(JSONB(), "postgresql")

tutor_threads = Table(
    "tutor_threads",
    tutoring_metadata,
    Column("thread_id", String(64), primary_key=True),
    Column("mistake_id", String(64), nullable=False),
    Column("learner_id", String(128), nullable=False),
    Column("stage", String(32), nullable=False, default="diagnose"),
    Column("summary", Text, nullable=False, default=""),
    Column("hint_level", Integer, nullable=False, default=0),
    Column("message_count", Integer, nullable=False, default=0),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

tutor_inputs = Table(
    "tutor_inputs",
    tutoring_metadata,
    Column("input_id", String(64), primary_key=True),
    Column("thread_id", String(64), ForeignKey("tutor_threads.thread_id", ondelete="SET NULL")),
    Column("mistake_id", String(64), nullable=False),
    Column("learner_id", String(128), nullable=False),
    Column("mode", String(32), nullable=False),
    Column("content", Text, nullable=False, default=""),
    Column("interaction_result_json", json_document, nullable=False, default=dict),
    Column("status", String(32), nullable=False),
    Column("observation_json", json_document),
    Column("formula_recognitions_json", json_document, nullable=False, default=dict),
    Column("canvas_state_json", json_document),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

tutor_artifacts = Table(
    "tutor_artifacts",
    tutoring_metadata,
    Column("artifact_id", String(64), primary_key=True),
    Column("input_id", String(64), ForeignKey("tutor_inputs.input_id", ondelete="CASCADE"), nullable=False),
    Column("thread_id", String(64)),
    Column("learner_id", String(128), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("media_type", String(255), nullable=False),
    Column("filename", Text, nullable=False),
    Column("byte_size", Integer, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("storage_path", Text, nullable=False),
    Column("created_at", Float, nullable=False),
)

tutor_observation_events = Table(
    "tutor_observation_events",
    tutoring_metadata,
    Column("event_id", String(64), primary_key=True),
    Column("input_id", String(64), ForeignKey("tutor_inputs.input_id", ondelete="CASCADE"), nullable=False),
    Column("learner_id", String(128), nullable=False),
    Column("decision", String(32), nullable=False),
    Column("payload_json", json_document, nullable=False, default=dict),
    Column("created_at", Float, nullable=False),
)

tutor_messages = Table(
    "tutor_messages",
    tutoring_metadata,
    Column("message_id", String(64), primary_key=True),
    Column(
        "thread_id",
        String(64),
        ForeignKey("tutor_threads.thread_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("input_id", String(64), ForeignKey("tutor_inputs.input_id", ondelete="SET NULL")),
    Column("role", String(16), nullable=False),
    Column("content", Text, nullable=False),
    Column("input_mode", String(32), nullable=False, default="text"),
    Column("assessment", String(32)),
    Column("action_json", json_document, nullable=False, default=dict),
    Column("model_run_json", json_document, nullable=False, default=dict),
    Column("created_at", Float, nullable=False),
)

Index("idx_tutor_threads_mistake", tutor_threads.c.mistake_id, tutor_threads.c.learner_id, unique=True)
Index("idx_tutor_threads_updated", tutor_threads.c.learner_id, tutor_threads.c.updated_at.desc())
Index("idx_tutor_messages_thread", tutor_messages.c.thread_id, tutor_messages.c.created_at)
Index("idx_tutor_inputs_thread", tutor_inputs.c.thread_id, tutor_inputs.c.created_at)
Index("idx_tutor_artifacts_input", tutor_artifacts.c.input_id, tutor_artifacts.c.created_at)
Index("idx_tutor_observation_events_input", tutor_observation_events.c.input_id, tutor_observation_events.c.created_at)

tutor_tool_events = Table(
    "tutor_tool_events",
    tutoring_metadata,
    Column("event_id", String(64), primary_key=True),
    Column("thread_id", String(64), ForeignKey("tutor_threads.thread_id", ondelete="SET NULL")),
    Column("input_id", String(64), ForeignKey("tutor_inputs.input_id", ondelete="SET NULL")),
    Column("learner_id", String(128), nullable=False),
    Column("tool_name", String(64), nullable=False),
    Column("proposal_json", json_document, nullable=False, default=dict),
    Column("policy_version", String(64), nullable=False),
    Column("decision", String(16), nullable=False),
    Column("reason", Text, nullable=False, default=""),
    Column("execution_status", String(32), nullable=False, default="shadow"),
    Column("result_ref_json", json_document, nullable=False, default=dict),
    Column("idempotency_key", String(128), nullable=False),
    Column("created_at", Float, nullable=False),
)

Index("idx_tutor_tool_events_thread", tutor_tool_events.c.thread_id, tutor_tool_events.c.created_at)
Index("idx_tutor_tool_events_idempotency", tutor_tool_events.c.idempotency_key, unique=True)


class TutoringStore:
    """Persist tutoring state without coupling it to textbook upload records.

    ``tutor_threads`` stores the small current state used to resume a lesson;
    ``tutor_messages`` is the append-only turn history used for display and
    auditing. ``append_turn`` writes both sides plus the state update in one
    transaction, so clients never observe half of a completed turn.
    """

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

    def create_or_get(self, mistake_id: str, learner_id: str = DEMO_LEARNER_ID) -> dict[str, Any]:
        """Return the one thread allowed for a learner/mistake pair."""
        self._ensure_initialized()
        existing = self.find_for_mistake(mistake_id, learner_id)
        if existing:
            return existing
        now = time.time()
        thread_id = uuid.uuid4().hex
        try:
            with self.engine.begin() as connection:
                connection.execute(tutor_threads.insert().values(
                    thread_id=thread_id,
                    mistake_id=mistake_id,
                    learner_id=learner_id,
                    stage="diagnose",
                    summary="",
                    hint_level=0,
                    message_count=0,
                    created_at=now,
                    updated_at=now,
                ))
        except Exception:
            # A concurrent create may win the unique mistake/learner key.
            existing = self.find_for_mistake(mistake_id, learner_id)
            if existing:
                return existing
            raise
        return self.get(thread_id) or {}

    def find_for_mistake(self, mistake_id: str, learner_id: str) -> dict[str, Any] | None:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(tutor_threads).where(
                    tutor_threads.c.mistake_id == mistake_id,
                    tutor_threads.c.learner_id == learner_id,
                )
            ).mappings().first()
        return self._serialize_thread(row) if row else None

    def get(self, thread_id: str, *, message_limit: int = 40) -> dict[str, Any] | None:
        """Load thread state and a bounded tail of display messages."""
        self._ensure_initialized()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(tutor_threads).where(tutor_threads.c.thread_id == thread_id)
            ).mappings().first()
            if not row:
                return None
            messages = connection.execute(
                select(tutor_messages)
                .where(tutor_messages.c.thread_id == thread_id)
                .order_by(tutor_messages.c.created_at.desc())
                .limit(max(1, min(message_limit, 100)))
            ).mappings().all()
        item = self._serialize_thread(row)
        item["messages"] = [self._serialize_message(message) for message in reversed(messages)]
        return item

    def recent_messages(self, thread_id: str, limit: int = 6) -> list[dict[str, Any]]:
        """Return the small recent-message window permitted in model context."""
        thread = self.get(thread_id, message_limit=limit)
        return thread["messages"] if thread else []

    def create_input(
        self,
        *,
        thread_id: str,
        mistake_id: str,
        learner_id: str,
        mode: str,
        content: str,
        interaction_result: dict[str, Any],
        status: str,
        observation: dict[str, Any] | None,
        formula_recognitions: list[dict[str, Any]] | None = None,
        canvas_state: dict[str, Any] | None = None,
        artifact: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist an input and its immutable artifact metadata atomically."""
        input_id = uuid.uuid4().hex
        now = time.time()
        with self.engine.begin() as connection:
            connection.execute(tutor_inputs.insert().values(
                input_id=input_id, thread_id=thread_id, mistake_id=mistake_id, learner_id=learner_id,
                mode=mode, content=content, interaction_result_json=interaction_result,
                status=status, observation_json=observation,
                formula_recognitions_json=formula_recognitions or [], canvas_state_json=canvas_state,
                created_at=now, updated_at=now,
            ))
            if artifact:
                artifact_values = {**artifact, "input_id": input_id}
                connection.execute(tutor_artifacts.insert().values(**artifact_values))
        return self.get_input(input_id) or {}

    def get_input(self, input_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(tutor_inputs).where(tutor_inputs.c.input_id == input_id)).mappings().first()
            if not row:
                return None
            artifacts = connection.execute(
                select(tutor_artifacts).where(tutor_artifacts.c.input_id == input_id).order_by(tutor_artifacts.c.created_at)
            ).mappings().all()
            events = connection.execute(
                select(tutor_observation_events).where(tutor_observation_events.c.input_id == input_id).order_by(tutor_observation_events.c.created_at)
            ).mappings().all()
        item = self._serialize_input(row)
        item["artifacts"] = [self._serialize_artifact(value) for value in artifacts]
        item["observationEvents"] = [self._serialize_event(value) for value in events]
        return item

    def append_observation_decision(
        self, input_id: str, *, learner_id: str, decision: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        current = self.get_input(input_id)
        if not current or current["learnerId"] != learner_id:
            return None
        if decision not in {"confirm", "correct", "reject"}:
            raise ValueError("未知的观察决策")
        status = {"confirm": "confirmed", "correct": "confirmed", "reject": "rejected"}[decision]
        now = time.time()
        with self.engine.begin() as connection:
            connection.execute(tutor_observation_events.insert().values(
                event_id=uuid.uuid4().hex, input_id=input_id, learner_id=learner_id,
                decision=decision, payload_json=payload, created_at=now,
            ))
            connection.execute(tutor_inputs.update().where(tutor_inputs.c.input_id == input_id).values(
                status=status, updated_at=now,
            ))
        return self.get_input(input_id)

    def append_tool_event(
        self,
        *,
        thread_id: str | None,
        input_id: str | None,
        learner_id: str,
        tool_name: str,
        proposal: dict[str, Any],
        policy_version: str,
        decision: str,
        reason: str,
        execution_status: str = "shadow",
        result_ref: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Append one tool-policy decision without storing student content."""
        event_id = uuid.uuid4().hex
        now = time.time()
        key = idempotency_key or event_id
        with self.engine.connect() as connection:
            existing = connection.execute(
                select(tutor_tool_events).where(tutor_tool_events.c.idempotency_key == key)
            ).mappings().first()
        if existing:
            return self._serialize_tool_event(existing)
        with self.engine.begin() as connection:
            connection.execute(tutor_tool_events.insert().values(
                event_id=event_id, thread_id=thread_id, input_id=input_id,
                learner_id=learner_id, tool_name=tool_name, proposal_json=proposal,
                policy_version=policy_version, decision=decision, reason=reason,
                execution_status=execution_status, result_ref_json=result_ref or {},
                idempotency_key=key, created_at=now,
            ))
        return self._serialize_tool_event({
            "event_id": event_id, "thread_id": thread_id, "input_id": input_id,
            "learner_id": learner_id, "tool_name": tool_name, "proposal_json": proposal,
            "policy_version": policy_version, "decision": decision, "reason": reason,
            "execution_status": execution_status, "result_ref_json": result_ref or {},
            "idempotency_key": key, "created_at": now,
        })

    def list_tool_events(self, thread_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(tutor_tool_events)
                .where(tutor_tool_events.c.thread_id == thread_id)
                .order_by(tutor_tool_events.c.created_at.desc())
                .limit(max(1, min(limit, 200)))
            ).mappings().all()
        return [self._serialize_tool_event(row) for row in reversed(rows)]

    def delete_for_mistake(self, mistake_id: str, learner_id: str = DEMO_LEARNER_ID) -> int:
        """清理一道错题的陪练上下文，并返回删除的线程数。

        错题本的归档仍是软删除：题目和学习证据保留，列表默认隐藏。但归档后
        再次进入不应恢复一段已经失效的对话，因此显式删除消息和线程；消息表先删
        先删消息再删线程，保持删除顺序与外键约束一致。
        """
        self._ensure_initialized()
        with self.engine.begin() as connection:
            thread_ids = [
                row[0]
                for row in connection.execute(
                    select(tutor_threads.c.thread_id).where(
                        tutor_threads.c.mistake_id == mistake_id,
                        tutor_threads.c.learner_id == learner_id,
                    )
                ).all()
            ]
            if not thread_ids:
                return 0
            connection.execute(
                delete(tutor_messages).where(tutor_messages.c.thread_id.in_(thread_ids))
            )
            connection.execute(
                delete(tutor_threads).where(tutor_threads.c.thread_id.in_(thread_ids))
            )
        return len(thread_ids)

    def advance_stage(
        self,
        thread_id: str,
        stage: str,
        *,
        summary: str | None = None,
    ) -> dict[str, Any] | None:
        """Advance a thread without fabricating a chat turn.

        Variation answers are already persisted by ``VariationStore``.  They
        still need to move the tutoring state from ``practice`` to ``verify``;
        writing a synthetic student/assistant pair would pollute the dialogue,
        so this method updates only the durable state and enforces monotonicity.
        """
        stage_order = {"diagnose": 0, "explain": 1, "practice": 2, "verify": 3}
        if stage not in stage_order:
            raise ValueError(f"未知陪练阶段: {stage}")
        current = self.get(thread_id, message_limit=1)
        if not current:
            return None
        if stage_order[stage] < stage_order.get(current["stage"], 0):
            return self.get(thread_id)
        now = time.time()
        with self.engine.begin() as connection:
            values: dict[str, Any] = {"stage": stage, "updated_at": now}
            if summary is not None:
                values["summary"] = summary[-2_000:]
            connection.execute(
                tutor_threads.update()
                .where(tutor_threads.c.thread_id == thread_id)
                .values(**values)
            )
        return self.get(thread_id)

    def append_turn(
        self,
        thread_id: str,
        *,
        student_content: str,
        input_mode: str,
        assistant_content: str,
        assessment: str | None,
        action: dict[str, Any],
        model_run: dict[str, Any],
        stage: str,
        hint_level: int,
        summary: str,
        input_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Persist both sides of a turn and update thread state atomically."""
        self._ensure_initialized()
        if not self.get(thread_id, message_limit=1):
            return None
        now = time.time()
        with self.engine.begin() as connection:
            connection.execute(tutor_messages.insert(), [
                {
                    "message_id": uuid.uuid4().hex,
                    "thread_id": thread_id,
                    "input_id": input_id,
                    "role": "student",
                    "content": student_content,
                    "input_mode": input_mode,
                    "assessment": None,
                    "action_json": {},
                    "model_run_json": {},
                    "created_at": now,
                },
                {
                    "message_id": uuid.uuid4().hex,
                    "thread_id": thread_id,
                    "input_id": input_id,
                    "role": "assistant",
                    "content": assistant_content,
                    "input_mode": "text",
                    "assessment": assessment,
                    "action_json": action,
                    "model_run_json": model_run,
                    # Time is currently the stable display order. A tiny offset
                    # keeps the assistant after the learner on coarse clocks.
                    "created_at": now + 0.000001,
                },
            ])
            connection.execute(
                tutor_threads.update()
                .where(tutor_threads.c.thread_id == thread_id)
                .values(
                    stage=stage,
                    summary=summary[-2_000:],
                    hint_level=hint_level,
                    message_count=tutor_threads.c.message_count + 2,
                    updated_at=now,
                )
            )
        return self.get(thread_id)

    @staticmethod
    def _serialize_thread(row: Any) -> dict[str, Any]:
        return {
            "threadId": row["thread_id"],
            "mistakeId": row["mistake_id"],
            "learnerId": row["learner_id"],
            "stage": row["stage"],
            "summary": row["summary"],
            "hintLevel": row["hint_level"],
            "messageCount": row["message_count"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _serialize_message(row: Any) -> dict[str, Any]:
        return {
            "messageId": row["message_id"],
            "threadId": row["thread_id"],
            "inputId": row.get("input_id") if hasattr(row, "get") else None,
            "role": row["role"],
            "content": row["content"],
            "inputMode": row["input_mode"],
            "assessment": row["assessment"],
            "action": row["action_json"] or {},
            "modelRun": row["model_run_json"] or {},
            "createdAt": row["created_at"],
        }

    @staticmethod
    def _serialize_input(row: Any) -> dict[str, Any]:
        return {
            "inputId": row["input_id"], "schemaVersion": "tutor-input-v1", "threadId": row["thread_id"],
            "mistakeId": row["mistake_id"],
            "learnerId": row["learner_id"], "mode": row["mode"], "content": row["content"],
            "text": row["content"], "interactionResult": row["interaction_result_json"] or {},
            "structuredAnswer": row["interaction_result_json"] or {}, "status": row["status"],
            "observation": row["observation_json"], "artifacts": [],
            "formulaRecognitions": row["formula_recognitions_json"] or [],
            "canvasState": row["canvas_state_json"],
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _serialize_artifact(row: Any, *, include_storage_path: bool = False) -> dict[str, Any]:
        return {
            "artifactId": row["artifact_id"], "inputId": row["input_id"], "threadId": row["thread_id"] or "",
            "learnerId": row["learner_id"], "kind": row["kind"], "mediaType": row["media_type"],
            "filename": row["filename"], "byteSize": row["byte_size"], "sha256": row["sha256"],
            "storagePath": row["storage_path"] if include_storage_path else None,
            "createdAt": row["created_at"],
        }

    @staticmethod
    def _serialize_event(row: Any) -> dict[str, Any]:
        return {
            "eventId": row["event_id"], "inputId": row["input_id"], "learnerId": row["learner_id"],
            "decision": row["decision"], "payload": row["payload_json"] or {}, "createdAt": row["created_at"],
        }

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(tutor_artifacts).where(tutor_artifacts.c.artifact_id == artifact_id)).mappings().first()
        return self._serialize_artifact(row, include_storage_path=True) if row else None

    @staticmethod
    def _serialize_tool_event(row: Any) -> dict[str, Any]:
        return {
            "eventId": row["event_id"], "threadId": row.get("thread_id"),
            "inputId": row.get("input_id"), "learnerId": row["learner_id"],
            "toolName": row["tool_name"], "proposal": row["proposal_json"] or {},
            "policyVersion": row["policy_version"], "decision": row["decision"],
            "reason": row["reason"], "executionStatus": row["execution_status"],
            "resultRef": row["result_ref_json"] or {}, "idempotencyKey": row["idempotency_key"],
            "createdAt": row["created_at"],
        }

    def close(self) -> None:
        self.engine.dispose()
