"""Database URL and JSON value helpers.

This module contains infrastructure rules rather than product data access.  A
store can therefore reuse the same PostgreSQL/SQLite setup without importing
application-specific persistence composition.
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote


class DatabaseConfigurationError(RuntimeError):
    """Raised when a runtime database target was not configured explicitly."""


def normalize_database_url(value: str) -> str:
    """Use the psycopg 3 SQLAlchemy dialect for common PostgreSQL URLs."""
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value.removeprefix("postgres://")
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix("postgresql://")
    return value


def build_postgres_url_from_env() -> str:
    """Build an escaped password URL when POSTGRES_* variables are provided."""
    password = os.getenv("POSTGRES_PASSWORD", "")
    if not password:
        raise DatabaseConfigurationError(
            "未配置 PostgreSQL 数据库：请设置 DATABASE_URL，或设置 POSTGRES_PASSWORD "
            "并按需设置 POSTGRES_HOST、POSTGRES_PORT、POSTGRES_USER、POSTGRES_DB；"
            "DOTTY_DATA_DIR 只决定文件资产目录，不能作为数据库配置。"
        )
    user = quote(os.getenv("POSTGRES_USER", "dotty_app"), safe="")
    encoded_password = quote(password, safe="")
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = quote(os.getenv("POSTGRES_DB", "dotty_tutor"), safe="")
    sslmode = os.getenv("POSTGRES_SSLMODE", "")
    query = f"?sslmode={quote(sslmode, safe='')}" if sslmode else ""
    return f"postgresql+psycopg://{user}:{encoded_password}@{host}:{port}/{database}{query}"


def resolve_database_url(explicit_url: str | None = None) -> str:
    """Resolve an explicit target or a configured PostgreSQL environment.

    SQLite remains available only when passed as an explicit argument, which
    keeps isolated tests working without allowing ``DOTTY_DATA_DIR`` or an
    unset environment to silently select a local database for the runtime.
    """
    if explicit_url:
        normalized = normalize_database_url(explicit_url)
        if normalized.startswith("sqlite"):
            return normalized
        if not normalized.startswith("postgresql"):
            raise DatabaseConfigurationError(
                "数据库 URL 必须指向 PostgreSQL；过渡测试才可通过 database_url 参数显式传入 SQLite URL。"
            )
        return normalized
    configured_url = os.getenv("DATABASE_URL")
    if configured_url:
        normalized = normalize_database_url(configured_url)
        if not normalized.startswith("postgresql"):
            raise DatabaseConfigurationError(
                "DATABASE_URL 必须指向 PostgreSQL；过渡测试请通过 database_url 参数显式传入 SQLite URL。"
            )
        return normalized
    return build_postgres_url_from_env()


def decode_json(value: Any) -> Any:
    """Return a JSON value from either SQLAlchemy's native value or SQLite text."""
    if isinstance(value, str):
        return json.loads(value)
    return value
