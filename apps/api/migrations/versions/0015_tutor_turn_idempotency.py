"""Ensure the registered tutor-turn idempotency table and lookup index exist."""

from alembic import op
from sqlalchemy import inspect

from persistence.migration_support import (
    create_registered_schema,
    ensure_registered_indexes,
)
from persistence.tutoring_store import tutor_turn_requests

revision = "0015_tutor_turn_idempotency"
down_revision = "0014_protected_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    # Earlier revisions may have pre-created registered tables. Use the same
    # metadata reconciliation as those revisions to avoid duplicate CREATEs.
    create_registered_schema(connection)
    ensure_registered_indexes(connection)
    expected = next(
        index for index in tutor_turn_requests.indexes
        if index.name == "idx_tutor_turn_requests_thread"
    )
    actual = next(
        (
            item for item in inspect(connection).get_indexes(tutor_turn_requests.name)
            if item.get("name") == expected.name
        ),
        None,
    )
    if (
        actual is None
        or actual.get("column_names") != [column.name for column in expected.columns]
        or bool(actual.get("unique")) != bool(expected.unique)
    ):
        raise RuntimeError(f"索引 {expected.name} 与 metadata 不一致")


def downgrade() -> None:
    # Successful network retries are durable audit facts; do not erase them on downgrade.
    pass
