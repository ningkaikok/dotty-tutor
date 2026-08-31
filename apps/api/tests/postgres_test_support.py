"""安全创建和清理一次性 PostgreSQL 测试数据库。"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any

from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine, make_url

TEST_ADMIN_ENV = "DOTTY_TEST_POSTGRES_ADMIN_URL"
TEST_DATABASE_PREFIX = "dotty_ci_test_"


class PostgresTestConfigurationError(RuntimeError):
    """Raised when the isolated PostgreSQL test target is unsafe or invalid."""


def _admin_url_from_environment() -> URL:
    raw_url = os.getenv(TEST_ADMIN_ENV)
    if not raw_url:
        raise PostgresTestConfigurationError(
            f"{TEST_ADMIN_ENV} 未设置；PostgreSQL 集成测试需要显式 admin URL"
        )
    try:
        admin_url = make_url(raw_url)
    except Exception as error:
        raise PostgresTestConfigurationError(
            f"{TEST_ADMIN_ENV} 不是有效的 PostgreSQL URL"
        ) from error
    if admin_url.drivername == "postgresql":
        admin_url = admin_url.set(drivername="postgresql+psycopg")
    if admin_url.drivername != "postgresql+psycopg":
        raise PostgresTestConfigurationError(
            f"{TEST_ADMIN_ENV} 必须使用 postgresql+psycopg 驱动"
        )
    if not admin_url.database or admin_url.database.startswith(TEST_DATABASE_PREFIX):
        raise PostgresTestConfigurationError(
            f"{TEST_ADMIN_ENV} 必须指向维护数据库，不能指向测试库"
        )
    if admin_url.database in {"dotty_tutor", "production", "prod"}:
        raise PostgresTestConfigurationError(
            f"{TEST_ADMIN_ENV} 不能指向正式业务数据库"
        )
    runtime_url = os.getenv("DATABASE_URL")
    if runtime_url:
        try:
            runtime_target = make_url(runtime_url)
        except Exception as error:
            raise PostgresTestConfigurationError(
                "DATABASE_URL 不是有效的 PostgreSQL URL，无法安全启动集成测试"
            ) from error
        if runtime_target.drivername == "postgresql":
            runtime_target = runtime_target.set(drivername="postgresql+psycopg")
    else:
        runtime_target = None
    if runtime_target is not None and _same_database_url(admin_url, runtime_target):
        raise PostgresTestConfigurationError(
            f"{TEST_ADMIN_ENV} 不能复用 DATABASE_URL；请使用独立 PostgreSQL admin 库"
        )
    return admin_url


def _same_database_url(first: URL, second: URL) -> bool:
    """Compare targets without exposing or depending on password text."""
    return first.set(password=None).render_as_string(hide_password=True) == second.set(
        password=None
    ).render_as_string(hide_password=True)


@dataclass
class PostgresTestDatabase:
    """One disposable PostgreSQL database owned by the current test process."""

    admin_url: URL
    name: str
    engine: Engine | None = None
    _created: bool = False

    @classmethod
    def create(cls) -> PostgresTestDatabase:
        admin_url = _admin_url_from_environment()
        name = f"{TEST_DATABASE_PREFIX}{uuid.uuid4().hex[:16]}"
        admin_engine = create_engine(
            admin_url,
            future=True,
            isolation_level="AUTOCOMMIT",
        )
        try:
            with admin_engine.connect() as connection:
                raw_connection: Any = connection.connection.driver_connection
                raw_connection.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
                )
        finally:
            admin_engine.dispose()
        database = cls(admin_url=admin_url, name=name, _created=True)
        try:
            database.engine = create_engine(database.url, future=True)
        except Exception:
            database.close()
            raise
        return database

    @property
    def url(self) -> URL:
        """Return the SQLAlchemy URL for this test database without printing it."""
        return self.admin_url.set(database=self.name)

    @property
    def database_url(self) -> str:
        """Return the runtime URL for child stores."""
        return self.url.render_as_string(hide_password=False)

    def close(self) -> None:
        """Dispose connections, terminate own leftovers, then drop own database."""
        if self.engine is not None:
            self.engine.dispose()
            self.engine = None
        if not self._created:
            return
        if not self.name.startswith(TEST_DATABASE_PREFIX):
            raise PostgresTestConfigurationError("拒绝清理非本测试前缀数据库")
        admin_engine = create_engine(
            self.admin_url,
            future=True,
            isolation_level="AUTOCOMMIT",
        )
        try:
            with admin_engine.connect() as connection:
                raw_connection: Any = connection.connection.driver_connection
                raw_connection.execute(
                    sql.SQL(
                        "SELECT pg_terminate_backend(pid) "
                        "FROM pg_stat_activity "
                        "WHERE datname = {} AND pid <> pg_backend_pid()"
                    ).format(sql.Literal(self.name))
                )
                raw_connection.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(self.name))
                )
            self._created = False
        finally:
            admin_engine.dispose()

    def __enter__(self) -> PostgresTestDatabase:
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()


def postgres_tests_enabled() -> bool:
    """Whether the caller supplied the explicit isolated-test admin URL."""
    return bool(os.getenv(TEST_ADMIN_ENV))
