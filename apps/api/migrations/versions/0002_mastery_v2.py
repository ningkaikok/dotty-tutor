"""Upgrade the learning projection and preserve legacy mastery rows."""

from alembic import op

from persistence.migration_support import upgrade_mastery

revision = "0002_mastery_v2"
down_revision = "0001_adopt_current_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    upgrade_mastery(op.get_bind())


def downgrade() -> None:
    pass
