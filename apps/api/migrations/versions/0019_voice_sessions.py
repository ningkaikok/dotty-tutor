"""Add durable, learner-scoped voice tutoring conversations."""

from alembic import op

from persistence.migration_support import (
    create_registered_schema,
    ensure_registered_indexes,
)

revision = "0019_voice_sessions"
down_revision = "0018_lesson_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    create_registered_schema(connection)
    ensure_registered_indexes(connection)


def downgrade() -> None:
    # Conversation transcripts are recoverable user data; downgrades preserve them.
    pass
