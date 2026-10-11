"""Persist bounded model runs, blind assignments and human ratings."""

import sqlalchemy as sa
from alembic import op

from persistence.migration_support import (
    create_registered_schema,
    ensure_registered_indexes,
)

revision = "0021_quality_review_runs"
down_revision = "0020_quality_review_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    create_registered_schema(connection)
    ensure_registered_indexes(connection)
    connection.execute(sa.text(
        "INSERT INTO quality_review_run_control (control_id, active_run_id) "
        "VALUES ('active', NULL) ON CONFLICT (control_id) DO NOTHING"
    ))


def downgrade() -> None:
    # Evaluations are audit records; keep them available if application code rolls back.
    pass
