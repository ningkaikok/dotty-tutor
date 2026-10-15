"""Persist learner-owned courseware reading positions."""

from alembic import op

from persistence.migration_support import (
    create_registered_schema,
    ensure_registered_indexes,
)

revision = "0027_courseware_unit_progress"
down_revision = "0026_ai_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    create_registered_schema(connection)
    ensure_registered_indexes(connection)


def downgrade() -> None:
    # Keep learner history when application binaries are rolled back.
    pass
