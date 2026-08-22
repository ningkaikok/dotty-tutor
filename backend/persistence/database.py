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

from observability import log_event

DEFAULT_POSTGRES_URL = "postgresql+psycopg:///dotty_tutor"


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
        # 显式记录本地 Socket 回退；环境变量只配置一半，通常意味着开发者忘记加载 .env.local。
        log_event(
            "storage.postgres.socket_fallback",
            level=30,
            reason="POSTGRES_PASSWORD 未设置",
            url=DEFAULT_POSTGRES_URL,
            hint="如需连接 Docker/远程 PostgreSQL，请设置 POSTGRES_PASSWORD 等变量或 DATABASE_URL（本地开发用 scripts/dev-local.sh 会读取 .env.local）",
        )
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
    """Return a JSON value from either SQLAlchemy's native value or SQLite text."""
    if isinstance(value, str):
        return json.loads(value)
    return value
