"""Add persistent source-grounded quality review cases and decisions."""

from alembic import op

from persistence.migration_support import (
    create_registered_schema,
    ensure_registered_indexes,
)

revision = "0017_quality_reviews"
down_revision = "0016_chapter_courses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    create_registered_schema(connection)
    ensure_registered_indexes(connection)


def downgrade() -> None:
    # Review history is retained so an accidental downgrade cannot erase decisions.
    pass
