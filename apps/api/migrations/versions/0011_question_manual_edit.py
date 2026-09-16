"""Add manual-edit revision tracking and a current-revision pointer.

人工编辑/回滚题目需要两件事：区分一条 revision 是模型产生还是人工编辑产生
（``question_revisions.revision_source``），以及知道题目当前展示的是哪一条
revision（``batch_questions.current_revision_id``），才能做乐观并发校验和回滚。
两个字段都是纯附加列，不改写、不删除任何既有 revision 行，追加写入原则不变。
"""

from alembic import op
from sqlalchemy import text

from persistence.migration_support import (
    add_missing_columns,
    create_registered_schema,
    ensure_registered_indexes,
    table_names,
)

revision = "0011_question_manual_edit"
down_revision = "0010_tutor_search_and_tools"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    create_registered_schema(connection)
    add_missing_columns(connection)
    ensure_registered_indexes(connection)
    # 回填历史行的当前版本指针：迁移之前，"当前视图"一直隐式等于每道题最新的
    # revision。这里只是把这个既有事实显式写成指针，不改变任何题目的可见内容。
    if "batch_questions" in table_names(connection) and "question_revisions" in table_names(connection):
        connection.execute(
            text(
                """
                UPDATE batch_questions AS bq
                SET current_revision_id = latest.revision_id
                FROM (
                    SELECT DISTINCT ON (upload_id, source_question_key)
                        upload_id, source_question_key, revision_id
                    FROM question_revisions
                    ORDER BY upload_id, source_question_key, revision_number DESC
                ) AS latest
                WHERE bq.upload_id = latest.upload_id
                  AND bq.batch_id = latest.source_question_key
                  AND bq.current_revision_id IS NULL
                """
            )
        )


def downgrade() -> None:
    # 两个新列都是审计/并发辅助信息，且已被应用层依赖；保留数据，不做破坏性回滚。
    pass
