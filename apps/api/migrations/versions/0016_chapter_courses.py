"""Add durable chapter studio state and English evidence attempts."""

from alembic import op

from persistence.migration_support import (
    create_registered_schema,
    ensure_registered_indexes,
)

revision = "0016_chapter_courses"
down_revision = "0015_tutor_turn_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    create_registered_schema(connection)
    ensure_registered_indexes(connection)


def downgrade() -> None:
    # Course and attempt history is durable learning evidence; downgrade keeps it.
    pass
