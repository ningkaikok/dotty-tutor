"""Add immutable source-grounded lesson media revisions and review history."""

from alembic import op

from persistence.migration_support import (
    create_registered_schema,
    ensure_registered_indexes,
)

revision = "0018_lesson_media"
down_revision = "0017_quality_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    create_registered_schema(connection)
    ensure_registered_indexes(connection)


def downgrade() -> None:
    # Keep immutable media and its human review trail recoverable on downgrade.
    pass
