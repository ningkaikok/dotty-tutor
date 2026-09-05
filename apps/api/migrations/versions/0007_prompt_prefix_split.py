"""Record stable/dynamic prompt split chars on model call metrics."""

from alembic import op

from persistence.migration_support import add_missing_columns, create_registered_schema

revision = "0007_prompt_prefix_split"
down_revision = "0006_model_call_fallback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    create_registered_schema(connection)
    add_missing_columns(connection)


def downgrade() -> None:
    pass
