from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine
from observability import log_event


DEFAULT_POSTGRES_URL = "postgresql+psycopg:///dotty_tutor"

metadata = MetaData()
json_document = JSON().with_variant(JSONB(), "postgresql")

upload_jobs = Table(
    "upload_jobs",
    metadata,
    Column("upload_id", String(64), primary_key=True),
    Column("import_id", String(128)),
    Column("filename", Text, nullable=False),
    Column("content_type", String(255), nullable=False),
    Column("size", BigInteger, nullable=False),
    Column("chunk_size", Integer, nullable=False),
    Column("total_chunks", Integer, nullable=False),
    Column("source_text", Text, nullable=False, default=""),
    Column("directory", Text, nullable=False),
    Column("status", String(32), nullable=False),
    Column("progress", Integer, nullable=False, default=0),
    Column("message", Text, nullable=False, default=""),
    Column("result_json", json_document),
    Column("started_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    Column("completed_at", Float),
)

batch_questions = Table(
    "batch_questions",
    metadata,
    Column(
        "upload_id",
        String(64),
        ForeignKey("upload_jobs.upload_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("batch_id", String(128), primary_key=True),
    Column("question_id", String(128), nullable=False),
    Column("payload_json", json_document, nullable=False),
    Column("guide_cards_json", json_document, nullable=False, default=list),
    Column("created_at", Float, nullable=False),
)

lesson_documents = Table(
    "lesson_documents",
    metadata,
    Column("lesson_id", String(128), primary_key=True),
    Column("source_upload_id", String(64)),
    Column("title", Text, nullable=False),
    Column("version", Integer, nullable=False, default=1),
    Column("status", String(32), nullable=False, default="draft"),
    Column("knowledge_points_json", json_document, nullable=False, default=list),
    Column("blocks_json", json_document, nullable=False, default=list),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

learning_sessions = Table(
    "learning_sessions",
    metadata,
    Column("session_id", String(64), primary_key=True),
    Column("learner_id", String(128), nullable=False),
    Column("lesson_id", String(128), nullable=False),
    Column("started_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

exercise_attempts = Table(
    "exercise_attempts",
    metadata,
    Column("attempt_id", String(64), primary_key=True),
    Column(
        "session_id",
        String(64),
        ForeignKey("learning_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("question_id", String(128), nullable=False),
    Column("knowledge_point", String(160), nullable=False),
    Column("response_json", json_document, nullable=False, default=dict),
    Column("assessment", String(32), nullable=False),
    Column("hint_level", Integer, nullable=False, default=0),
    Column("duration_ms", Integer, nullable=False, default=0),
    Column("created_at", Float, nullable=False),
)

mastery_states = Table(
    "mastery_states",
    metadata,
    Column("learner_id", String(128), primary_key=True),
    Column("knowledge_point", String(160), primary_key=True),
    Column("score", Float, nullable=False, default=0),
    Column("attempt_count", Integer, nullable=False, default=0),
    Column("correct_count", Integer, nullable=False, default=0),
    Column("last_practiced_at", Float, nullable=False),
)

Index("idx_upload_jobs_updated", upload_jobs.c.updated_at.desc())
Index("idx_learning_sessions_learner", learning_sessions.c.learner_id, learning_sessions.c.updated_at.desc())
Index("idx_exercise_attempts_session", exercise_attempts.c.session_id, exercise_attempts.c.created_at.desc())


def normalize_database_url(value: str) -> str:
    """Use psycopg 3 for common PostgreSQL URL spellings."""
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value.removeprefix("postgres://")
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix("postgresql://")
    return value


def build_postgres_url_from_env() -> str:
    """Build an explicit password URL when POSTGRES_* variables are provided."""
    password = os.getenv("POSTGRES_PASSWORD", "")
    if not password:
        return DEFAULT_POSTGRES_URL
    user = quote(os.getenv("POSTGRES_USER", "dotty_app"), safe="")
    encoded_password = quote(password, safe="")
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = quote(os.getenv("POSTGRES_DB", "dotty_tutor"), safe="")
    sslmode = os.getenv("POSTGRES_SSLMODE", "")
    query = f"?sslmode={quote(sslmode, safe='')}" if sslmode else ""
    return f"postgresql+psycopg://{user}:{encoded_password}@{host}:{port}/{database}{query}"


def decode_json(value: Any) -> Any:
    """Read both native PostgreSQL JSONB values and legacy SQLite JSON text."""
    if isinstance(value, str):
        return json.loads(value)
    return value


class TutorStore:
    """Durable metadata store with PostgreSQL as the local and production default.

    PDFs and generated assets remain on disk. PostgreSQL stores jobs, question
    payloads, review results and guide cards. Explicit SQLite URLs remain
    supported so the legacy database can be read during migration and tests can
    stay isolated.
    """

    def __init__(
        self,
        database_url: str | None = None,
        data_root: str | Path | None = None,
    ) -> None:
        configured_root = data_root or os.getenv("DOTTY_DATA_DIR")
        self.root = (
            Path(configured_root).expanduser().resolve()
            if configured_root
            else Path(__file__).resolve().parents[1] / "data"
        )
        self.upload_root = self.root / "uploads"
        self.upload_root.mkdir(parents=True, exist_ok=True)

        configured_url = database_url or os.getenv("DATABASE_URL")
        # DOTTY_DATA_DIR historically selected an isolated SQLite database.
        # Preserve that explicit behavior for tests and legacy tooling, while
        # ordinary local development now defaults to PostgreSQL.
        if not configured_url and os.getenv("DOTTY_DATA_DIR"):
            configured_url = f"sqlite+pysqlite:///{self.root / 'dotty.sqlite3'}"
        self.database_url = normalize_database_url(configured_url or build_postgres_url_from_env())
        self.database_path = (
            Path(self.database_url.removeprefix("sqlite+pysqlite:///"))
            if self.database_url.startswith("sqlite+pysqlite:///")
            else None
        )
        connect_args: dict[str, Any] = {}
        if self.database_url.startswith("sqlite"):
            connect_args = {"check_same_thread": False, "timeout": 30}
        self.engine: Engine = create_engine(
            self.database_url,
            future=True,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        self._initialized = False
        self._initialize_lock = threading.Lock()

    @property
    def backend(self) -> str:
        return self.engine.dialect.name

    def ping(self) -> bool:
        """Check database connectivity without exposing driver details."""
        try:
            self._ensure_initialized()
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except Exception as error:
            log_event(
                "database.ping.failed",
                level=40,
                database=self.backend if hasattr(self, "engine") else "unknown",
                error_type=type(error).__name__,
                error=str(error)[:300],
                exc_info=True,
            )
            return False

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            metadata.create_all(self.engine)
            if self.backend == "sqlite":
                # Databases created before guide cards were persisted need one
                # small compatibility migration.
                with self.engine.begin() as connection:
                    columns = {
                        row[1]
                        for row in connection.exec_driver_sql(
                            "PRAGMA table_info(batch_questions)"
                        ).fetchall()
                    }
                    if "guide_cards_json" not in columns:
                        connection.exec_driver_sql(
                            "ALTER TABLE batch_questions "
                            "ADD COLUMN guide_cards_json TEXT NOT NULL DEFAULT '[]'"
                        )
            self._initialized = True

    def _upsert(
        self,
        connection: Connection,
        table: Table,
        values: dict[str, Any],
        conflict_columns: list[str],
        update_columns: list[str],
    ) -> None:
        if self.backend == "postgresql":
            statement = postgresql_insert(table).values(**values)
        elif self.backend == "sqlite":
            statement = sqlite_insert(table).values(**values)
        else:  # pragma: no cover - only configured backends are supported
            raise RuntimeError(f"不支持的数据库后端：{self.backend}")
        statement = statement.on_conflict_do_update(
            index_elements=[table.c[name] for name in conflict_columns],
            set_={name: getattr(statement.excluded, name) for name in update_columns},
        )
        connection.execute(statement)

    def _resolve_directory(self, stored_directory: str) -> Path:
        """Resolve paths persisted before the standalone project was moved."""
        candidate = Path(stored_directory).expanduser()
        if candidate.exists():
            return candidate
        parts = candidate.parts
        if "tutor-demo" in parts:
            marker = max(index for index, part in enumerate(parts) if part == "tutor-demo")
            migrated = self.root.parent.joinpath(*parts[marker + 1:])
            if migrated.exists():
                return migrated
        return candidate

    def save_job(self, job: dict[str, Any]) -> None:
        self._ensure_initialized()
        result = job.get("result")
        import_id = (
            result.get("importId")
            if isinstance(result, dict)
            else job.get("importId")
        )
        now = time.time()
        values = {
            "upload_id": job["uploadId"],
            "import_id": import_id,
            "filename": job["filename"],
            "content_type": job["contentType"],
            "size": job["size"],
            "chunk_size": job["chunkSize"],
            "total_chunks": job["totalChunks"],
            "source_text": job.get("sourceText", ""),
            "directory": str(job["directory"]),
            "status": job["status"],
            "progress": job.get("progress", 0),
            "message": job.get("message", ""),
            "result_json": result,
            "started_at": job.get("startedAt", now),
            "updated_at": job.get("updatedAt", now),
            "completed_at": job.get("completedAt"),
        }
        with self.engine.begin() as connection:
            self._upsert(
                connection,
                upload_jobs,
                values,
                ["upload_id"],
                [
                    "import_id", "filename", "content_type", "size", "chunk_size",
                    "total_chunks", "source_text", "directory", "status", "progress",
                    "message", "result_json", "updated_at", "completed_at",
                ],
            )

    def load_job(self, upload_id: str) -> dict[str, Any] | None:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(upload_jobs).where(upload_jobs.c.upload_id == upload_id)
            ).mappings().first()
            if not row:
                return None
            question_rows = connection.execute(
                select(
                    batch_questions.c.batch_id,
                    batch_questions.c.payload_json,
                    batch_questions.c.guide_cards_json,
                )
                .where(batch_questions.c.upload_id == upload_id)
                .order_by(batch_questions.c.created_at, batch_questions.c.batch_id)
            ).mappings().all()
        result = decode_json(row["result_json"]) if row["result_json"] is not None else None
        return {
            "uploadId": row["upload_id"],
            "importId": row["import_id"],
            "filename": row["filename"],
            "contentType": row["content_type"],
            "size": row["size"],
            "chunkSize": row["chunk_size"],
            "totalChunks": row["total_chunks"],
            "sourceText": row["source_text"],
            "directory": self._resolve_directory(row["directory"]),
            "status": row["status"],
            "progress": row["progress"],
            "message": row["message"],
            "startedAt": row["started_at"],
            "updatedAt": row["updated_at"],
            "completedAt": row["completed_at"],
            "result": result,
            "batchPayloads": {
                item["batch_id"]: decode_json(item["payload_json"])
                for item in question_rows
            },
            "batchGuideCards": {
                item["batch_id"]: decode_json(item["guide_cards_json"])
                for item in question_rows
            },
            "processingBatches": set(),
        }

    def save_question(
        self,
        upload_id: str,
        batch_id: str,
        payload: dict[str, Any],
        guide_cards: list[dict[str, Any]] | None = None,
    ) -> None:
        self.save_questions(upload_id, [(batch_id, payload, guide_cards or [])])

    def save_questions(
        self,
        upload_id: str,
        questions: list[tuple[str, dict[str, Any], list[dict[str, Any]]]],
    ) -> None:
        """Atomically insert or replace all generated questions in one call."""
        self._ensure_initialized()
        now = time.time()
        with self.engine.begin() as connection:
            for batch_id, payload, guide_cards in questions:
                self._upsert(
                    connection,
                    batch_questions,
                    {
                        "upload_id": upload_id,
                        "batch_id": batch_id,
                        "question_id": payload["question"]["id"],
                        "payload_json": payload,
                        "guide_cards_json": guide_cards,
                        "created_at": now,
                    },
                    ["upload_id", "batch_id"],
                    ["question_id", "payload_json", "guide_cards_json", "created_at"],
                )

    def list_imports(self) -> list[dict[str, Any]]:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(
                    upload_jobs.c.upload_id,
                    upload_jobs.c.import_id,
                    upload_jobs.c.filename,
                    upload_jobs.c.size,
                    upload_jobs.c.status,
                    upload_jobs.c.result_json,
                    upload_jobs.c.started_at,
                    upload_jobs.c.updated_at,
                )
                .where(
                    upload_jobs.c.status == "complete",
                    upload_jobs.c.result_json.is_not(None),
                )
                .order_by(upload_jobs.c.updated_at.desc())
            ).mappings().all()
        items = []
        for row in rows:
            result = decode_json(row["result_json"])
            extraction = result.get("extraction", {})
            items.append({
                "uploadId": row["upload_id"],
                "importId": row["import_id"],
                "filename": row["filename"],
                "size": row["size"],
                "status": row["status"],
                "questionCount": extraction.get("questionCount", 0),
                "pageCount": extraction.get("pageCount"),
                "chapter": extraction.get("chapter", "教材练习"),
                "updatedAt": row["updated_at"],
            })
        return items

    def soft_delete_import(self, upload_id: str) -> bool:
        """Mark an import as deleted so it drops out of the library.

        The row, generated questions and lesson data are kept intact, so the
        textbook stays recoverable; only ``status`` moves to ``deleted`` and
        ``list_imports`` already filters to ``complete`` records.
        """
        self._ensure_initialized()
        now = time.time()
        with self.engine.begin() as connection:
            result = connection.execute(
                upload_jobs.update()
                .where(
                    upload_jobs.c.upload_id == upload_id,
                    upload_jobs.c.status != "deleted",
                )
                .values(status="deleted", updated_at=now)
            )
        return result.rowcount > 0

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
                select(learning_sessions).where(learning_sessions.c.session_id == session_id)
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
            correct_count = int(current["correct_count"]) + (1 if assessment == "correct" else 0) if current else (1 if assessment == "correct" else 0)
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
        from sqlalchemy import func

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

    def close(self) -> None:
        self.engine.dispose()


store = TutorStore()
