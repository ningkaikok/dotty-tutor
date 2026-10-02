"""Archive prompt revisions and release pointers without modifying learning data."""

from alembic import op

from persistence.schema import prompt_heads, prompt_release_events, prompt_revisions

revision = "0013_prompt_management"
down_revision = "0012_learning_policy_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    for table in (prompt_revisions, prompt_heads, prompt_release_events):
        table.create(connection, checkfirst=True)


def downgrade() -> None:
    # 修订与发布审计留存，不通过降级删除历史教学规则。
    pass
