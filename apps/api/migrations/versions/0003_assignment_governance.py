"""Ensure class assignment and assignment-plan schema is present."""

from alembic import op

from persistence.migration_support import upgrade_assignments

revision = "0003_assignment_governance"
down_revision = "0002_mastery_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    upgrade_assignments(op.get_bind())


def downgrade() -> None:
    pass
