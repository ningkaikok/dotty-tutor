"""Add append-only multimodal TutorInput evidence tables."""

from alembic import op

from persistence.migration_support import (
    add_missing_columns,
    create_registered_schema,
    ensure_registered_indexes,
)

revision = "0008_tutor_multimodal_input"
down_revision = "0007_prompt_prefix_split"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    # Binary files remain in MistakeStore's asset directory; this migration only
    # creates references, observations and append-only decision events.
    create_registered_schema(connection)
    add_missing_columns(connection)
    ensure_registered_indexes(connection)


def downgrade() -> None:
    # Evidence is intentionally not destructively downgraded.
    pass
