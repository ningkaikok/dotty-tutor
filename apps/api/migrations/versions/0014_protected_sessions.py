"""Add constraints and expiry indexes for registered protected-session tables."""

from __future__ import annotations

from alembic import op
from sqlalchemy import CheckConstraint, inspect

from persistence.auth_store import auth_invites, auth_sessions
from persistence.migration_support import (
    create_registered_schema,
    ensure_registered_indexes,
)

revision = "0014_protected_sessions"
down_revision = "0013_prompt_management"
branch_labels = None
depends_on = None


def _ensure_session_checks() -> None:
    connection = op.get_bind()
    reflected_names = {
        str(item.get("name"))
        for item in inspect(connection).get_check_constraints("auth_sessions")
    }
    expected = []
    for constraint in auth_sessions.constraints:
        if not isinstance(constraint, CheckConstraint):
            continue
        name = str(constraint.name)
        if name not in {"ck_auth_sessions_role", "ck_auth_sessions_identity"}:
            continue
        expected.append((name, str(constraint.sqltext)))
    if connection.dialect.name == "sqlite":
        with op.batch_alter_table("auth_sessions") as batch:
            for name, expression in expected:
                if name in reflected_names:
                    batch.drop_constraint(name, type_="check")
                batch.create_check_constraint(name, expression)
    elif connection.dialect.name == "postgresql":
        for name, expression in expected:
            if name in reflected_names:
                op.drop_constraint(name, "auth_sessions", type_="check")
            # PostgreSQL reflection normalizes CHECK expressions (for example,
            # IN becomes ANY plus casts). Recreate only our named invariants from
            # the canonical metadata; ADD CONSTRAINT validates every existing row
            # and aborts this migration transaction if stored identities violate it.
            op.create_check_constraint(name, "auth_sessions", expression)
    else:
        raise RuntimeError(f"尚不支持在 {connection.dialect.name} 补齐 auth_sessions 检查约束")
    expected_names = {name for name, _expression in expected}
    actual_names = {
        str(item.get("name"))
        for item in inspect(connection).get_check_constraints("auth_sessions")
    }
    if not expected_names.issubset(actual_names):
        raise RuntimeError("迁移后 auth_sessions 检查约束与 metadata 不一致")


def _verify_index(connection, table, index_name: str) -> None:
    expected = next(index for index in table.indexes if index.name == index_name)
    actual = next(
        (item for item in inspect(connection).get_indexes(table.name) if item.get("name") == index_name),
        None,
    )
    expected_columns = [column.name for column in expected.columns]
    if (
        actual is None
        or actual.get("column_names") != expected_columns
        or bool(actual.get("unique")) != bool(expected.unique)
    ):
        raise RuntimeError(f"索引 {index_name} 与 metadata 不一致")


def upgrade() -> None:
    connection = op.get_bind()
    # Earlier schema revisions create all registered tables from current metadata.
    # Reconcile the two auth invariants explicitly so an existing pre-created
    # table is not mistaken for a fully constrained table.
    create_registered_schema(connection)
    _ensure_session_checks()
    ensure_registered_indexes(connection)
    _verify_index(connection, auth_sessions, "idx_auth_sessions_expiry")
    _verify_index(connection, auth_invites, "idx_auth_invites_expiry")


def downgrade() -> None:
    # Session history remains available for audit; downgrade is intentionally non-destructive.
    pass
