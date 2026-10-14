"""Add append-only AI pre-review verdicts for courseware pages and source cases."""
from alembic import op

from persistence.migration_support import (
    add_missing_columns,
    create_registered_schema,
    ensure_registered_indexes,
)

revision = "0026_ai_reviews"
down_revision = "0025_courseware_voice_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    create_registered_schema(connection)
    add_missing_columns(connection)
    ensure_registered_indexes(connection)


def downgrade() -> None:
    # Advisory verdicts are audit history; keep them rather than dropping evidence.
    pass
