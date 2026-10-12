"""Add reversible manifests for retiring old textbook-derived content."""

from alembic import op

from persistence.migration_support import (
    create_registered_schema,
    ensure_registered_indexes,
)

revision = "0024_legacy_textbook_cleanup"
down_revision = "0023_courseware"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the additive tombstone table from the registered metadata."""
    connection = op.get_bind()
    create_registered_schema(connection)
    ensure_registered_indexes(connection)


def downgrade() -> None:
    # The manifest is the recovery and audit source for exact legacy tombstones.
    pass
