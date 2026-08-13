"""PostgreSQL/SQLite persistence for tutor threads and bounded messages."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from sqlalchemy import Column, Float, ForeignKey, Index, Integer, JSON, MetaData, String, Table, Text, create_engine, delete, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine


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


class TutoringStore:
    """Persist tutoring state without coupling it to textbook upload records.

    ``tutor_threads`` stores the small current state used to resume a lesson;
    ``tutor_messages`` is the append-only turn history used for display and
    auditing. ``append_turn`` writes both sides plus the state update in one
    transaction, so clients never observe half of a completed turn.
    """

    def __init__(self, *, engine: Engine | None = None, database_url: str | None = None) -> None:
        if engine is None and not database_url:
            raise ValueError("engine 或 database_url 必须提供一个")
        self.engine = engine if engine is not None else create_engine(database_url, future=True)
        self._initialized = False
        self._initialize_lock = threading.Lock()

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            tutoring_metadata.create_all(self.engine)
            self._initialized = True

    def create_or_get(self, mistake_id: str, learner_id: str = "local-demo") -> dict[str, Any]:
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

    def delete_for_mistake(self, mistake_id: str, learner_id: str = "local-demo") -> int:
        """清理一道错题的陪练上下文，并返回删除的线程数。

        错题本的归档仍是软删除：题目和学习证据保留，列表默认隐藏。但归档后
        再次进入不应恢复一段已经失效的对话，因此显式删除消息和线程；消息表先删
        是为了兼容未开启外键级联的 SQLite 本地数据库。
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
            "role": row["role"],
            "content": row["content"],
            "inputMode": row["input_mode"],
            "assessment": row["assessment"],
            "action": row["action_json"] or {},
            "modelRun": row["model_run_json"] or {},
            "createdAt": row["created_at"],
        }

    def close(self) -> None:
        self.engine.dispose()
