"""Track private remote object manifests for recoverable application files."""

from alembic import op

from persistence.migration_support import (
    create_registered_schema,
    ensure_registered_indexes,
)

revision = "0028_managed_file_storage"
down_revision = "0027_courseware_unit_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    create_registered_schema(connection)
    ensure_registered_indexes(connection)


def downgrade() -> None:
    # File manifests and learning sources stay recoverable through binary rollback.
    pass
