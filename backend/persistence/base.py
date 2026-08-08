"""Shared database lifecycle used by the domain stores.

The application has several persistence domains, but they must share one
SQLAlchemy engine and one schema initialization path.  Keeping that plumbing in
this small base class avoids duplicating connection policy in every store while
leaving domain queries in domain-specific modules.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.sql.schema import Table

from observability import log_event
from persistence.database import build_postgres_url_from_env, normalize_database_url
from persistence.schema import metadata


class DatabaseStore:
    """Own the engine, data directories and cross-database upsert behavior."""

    def __init__(
        self,
        database_url: str | None = None,
        data_root: str | Path | None = None,
    ) -> None:
        configured_root = data_root or os.getenv("DOTTY_DATA_DIR")
        self.root = (
            Path(configured_root).expanduser().resolve()
            if configured_root
            else Path(__file__).resolve().parents[2] / "data"
        )
        self.upload_root = self.root / "uploads"
        self.upload_root.mkdir(parents=True, exist_ok=True)

        configured_url = database_url or os.getenv("DATABASE_URL")
        # DOTTY_DATA_DIR historically selected an isolated SQLite database.
        # Preserve that behavior for tests and legacy migration tools.
        if not configured_url and os.getenv("DOTTY_DATA_DIR"):
            configured_url = f"sqlite+pysqlite:///{self.root / 'dotty.sqlite3'}"
        self.database_url = normalize_database_url(
            configured_url or build_postgres_url_from_env()
        )
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
        """Execute one portable PostgreSQL/SQLite upsert."""
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

    def close(self) -> None:
        self.engine.dispose()
