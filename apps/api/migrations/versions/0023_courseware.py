"""Add courseware records, immutable publications, and optional learner attempts."""

from alembic import op

from persistence.migration_support import (
    create_registered_schema,
    ensure_registered_indexes,
)

revision = "0023_courseware"
down_revision = "0022_quality_review_compat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create only the additive courseware tables from registered metadata."""
    connection = op.get_bind()
    create_registered_schema(connection)
    ensure_registered_indexes(connection)


def downgrade() -> None:
    # Publications and student attempts are durable learning evidence.
    pass
