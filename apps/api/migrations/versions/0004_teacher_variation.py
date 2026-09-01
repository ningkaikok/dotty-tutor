"""Ensure teacher review events and variation attribution provenance."""

from alembic import op

from persistence.migration_support import upgrade_teacher_review, upgrade_variation

revision = "0004_teacher_variation"
down_revision = "0003_assignment_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    upgrade_teacher_review(op.get_bind())
    upgrade_variation(op.get_bind())


def downgrade() -> None:
    pass
