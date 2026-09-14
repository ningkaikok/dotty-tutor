"""Add structured canvas fields and append-only Tutor tool audit events."""

from alembic import op

from persistence.migration_support import (
    add_missing_columns,
    create_registered_schema,
    ensure_registered_indexes,
)

revision = "0009_tutor_tools_and_canvas"
down_revision = "0008_tutor_multimodal_input"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    create_registered_schema(connection)
    add_missing_columns(connection)
    ensure_registered_indexes(connection)


def downgrade() -> None:
    # Tutor evidence and policy events are append-only and are not destructively downgraded.
    pass
