"""集中注册各持久化领域的 SQLAlchemy metadata。

Store 仍然按领域拆分；registry 只负责让 SQLite 初始化、Alembic
autogenerate 和 schema readiness 检查看到同一组表。导入时显式检查重复表名，
避免两个领域声明同名表后由导入顺序静默决定事实来源。
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import MetaData, Table
from sqlalchemy.engine import Engine

from persistence.metrics_store import metadata as metrics_metadata
from persistence.mistake_store import mistake_metadata
from persistence.review_store import review_metadata
from persistence.schema import metadata as core_metadata
from persistence.tutoring_store import tutoring_metadata
from persistence.variation_store import variation_metadata

SCHEMA_METADATA: tuple[MetaData, ...] = (
    core_metadata,
    mistake_metadata,
    tutoring_metadata,
    variation_metadata,
    review_metadata,
    metrics_metadata,
)

DOMAIN_METADATA: dict[str, MetaData] = {
    "core": core_metadata,
    "mistake": mistake_metadata,
    "tutoring": tutoring_metadata,
    "variation": variation_metadata,
    "review": review_metadata,
    "metrics": metrics_metadata,
}


def table_registry() -> dict[str, Table]:
    """Return every registered table and fail fast on duplicate names."""
    tables: dict[str, Table] = {}
    owners: dict[str, str] = {}
    for domain, schema in DOMAIN_METADATA.items():
        for name, table in schema.tables.items():
            if name in tables:
                raise RuntimeError(
                    f"重复的 schema 表名 {name!r}: {owners[name]} 与 {domain}"
                )
            tables[name] = table
            owners[name] = domain
    return tables


def iter_metadata(*, exclude_tables: Iterable[str] = ()) -> Iterable[tuple[MetaData, list[Table]]]:
    """Yield metadata and tables, optionally excluding named tables."""
    excluded = set(exclude_tables)
    table_registry()
    for schema in SCHEMA_METADATA:
        tables = [table for table in schema.tables.values() if table.name not in excluded]
        if tables:
            yield schema, tables


def initialize_sqlite_schema(engine: Engine) -> None:
    """Initialize an isolated SQLite test database from the shared registry.

    Production PostgreSQL databases must be changed only through Alembic. The
    backend guard makes accidentally reusing this convenience path against a
    shared database fail loudly instead of silently changing its schema.
    """
    if engine.dialect.name != "sqlite":
        raise RuntimeError("只有隔离 SQLite 才允许使用自动 schema 初始化")
    for schema, tables in iter_metadata():
        schema.create_all(engine, tables=tables, checkfirst=True)


# Runtime health uses this stable value without importing Alembic's command
# layer. The migration files and the CLI use the same revision identifier.
SCHEMA_HEAD_REVISION = "0005_mistake_attributions"
