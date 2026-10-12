"""Add explicit courseware scope without rebinding historical voice conversations."""
from alembic import op

from persistence.migration_support import (
    add_missing_columns,
    create_registered_schema,
    ensure_registered_indexes,
)

revision = "0025_courseware_voice_context"
down_revision = "0024_legacy_textbook_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    create_registered_schema(connection)
    add_missing_columns(connection)
    ensure_registered_indexes(connection)
    # Existing databases keep their narrower constraint until this migration.
    op.drop_constraint("ck_lesson_media_subject", "lesson_media_revisions", type_="check")
    op.create_check_constraint("ck_lesson_media_subject", "lesson_media_revisions", "subject IN ('math', 'english', 'science')")


def downgrade() -> None:
    # Preserve recoverable conversations and their source identity.
    pass
