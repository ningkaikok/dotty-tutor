"""Add PostgreSQL-backed Tutor search documents and GIN index."""

from alembic import op

from persistence.migration_support import (
    add_missing_columns,
    create_registered_schema,
    ensure_registered_indexes,
)

revision = "0010_tutor_search_and_tools"
down_revision = "0009_tutor_tools_and_canvas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    create_registered_schema(connection)
    add_missing_columns(connection)
    ensure_registered_indexes(connection)


def downgrade() -> None:
    # Search documents can be rebuilt and are intentionally retained on downgrade.
    pass
