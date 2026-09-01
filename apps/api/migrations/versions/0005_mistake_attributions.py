"""Add append-only mistake attribution history and backfill legacy columns."""

from alembic import op

from persistence.migration_support import upgrade_mistake_attributions

revision = "0005_mistake_attributions"
down_revision = "0004_teacher_variation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    upgrade_mistake_attributions(op.get_bind())


def downgrade() -> None:
    pass
