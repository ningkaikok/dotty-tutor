"""Adopt v0.27.0 databases and create the registered schema."""

from alembic import op

from persistence.migration_support import ensure_current_schema

revision = "0001_adopt_current_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    ensure_current_schema(op.get_bind(), include_attributions=False)


def downgrade() -> None:
    # This project deliberately has no destructive downgrade for data-bearing tables.
    pass
