"""各领域 Store 共享的 PostgreSQL 连接生命周期。

应用包含教材、学习、错题和复习等多个持久化领域，但它们必须共享一个 SQLAlchemy Engine。
公共连接策略放在这个小基类，领域查询仍留在各自 Store，避免重新形成万能仓库。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.sql.schema import Table

from observability import log_event
from persistence.database import resolve_database_url


class DatabaseStore:
    """统一管理 PostgreSQL Engine 和文件资产目录。"""

    def __init__(
        self,
        database_url: str | None = None,
        data_root: str | Path | None = None,
    ) -> None:
        self.database_url = resolve_database_url(database_url)
        configured_root = data_root or os.getenv("DOTTY_DATA_DIR")
        self.root = (
            Path(configured_root).expanduser().resolve()
            if configured_root
            else (
                # monorepo 布局：persistence → api → apps → 仓库根。
                Path(__file__).resolve().parents[3] / "data"
            )
        )
        self.upload_root = self.root / "uploads"
        self.upload_root.mkdir(parents=True, exist_ok=True)

        self.engine: Engine = create_engine(
            self.database_url,
            future=True,
            pool_pre_ping=True,
        )

    @property
    def backend(self) -> str:
        return self.engine.dialect.name

    def ping(self) -> bool:
        """检查数据库连通性，并把驱动异常收敛为布尔健康状态。"""
        try:
            self._ensure_initialized()
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            from persistence.migration_support import schema_is_ready

            return schema_is_ready(self.engine, require_version=True)
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
        """Keep the store call sites uniform; schema creation is Alembic-owned."""
        return None

    def schema_status(self) -> dict[str, Any]:
        """Return a sanitized schema/readiness report for health and diagnostics."""
        self._ensure_initialized()
        from persistence.migration_support import schema_report

        return schema_report(self.engine, require_version=True)

    def _upsert(
        self,
        connection: Connection,
        table: Table,
        values: dict[str, Any],
        conflict_columns: list[str],
        update_columns: list[str],
    ) -> None:
        """执行一条 PostgreSQL Upsert。"""
        statement = postgresql_insert(table).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[table.c[name] for name in conflict_columns],
            set_={name: getattr(statement.excluded, name) for name in update_columns},
        )
        connection.execute(statement)

    def close(self) -> None:
        self.engine.dispose()
