"""Add versioned mastery policy metadata and auditable review schedules."""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect, text

from persistence.migration_support import (
    add_missing_columns,
    create_registered_schema,
    ensure_registered_indexes,
    ensure_review_task_compatibility,
    table_names,
)

revision = "0012_learning_policy_schedule"
down_revision = "0011_question_manual_edit"
branch_labels = None
depends_on = None


def _backfill_review_rows() -> None:
    connection = op.get_bind()
    if "review_tasks" not in table_names(connection):
        return
    # Existing rows retain the old schedule semantics. Sequence numbers are
    # reconstructed in interval order (1/3/7 become 1/2/3), only when the new
    # column was introduced with its zero default.
    connection.execute(text(
        "UPDATE review_tasks SET schedule_version = COALESCE(schedule_version, 'legacy-1-3-7-v1'), "
        "profile = COALESCE(profile, 'unknown:legacy'), "
        "sequence_no = CASE WHEN sequence_no = 0 THEN ("
        "SELECT COUNT(*) FROM review_tasks AS prior "
        "WHERE prior.mistake_id = review_tasks.mistake_id "
        "AND prior.interval_days <= review_tasks.interval_days"
        ") ELSE sequence_no END "
        "WHERE schedule_version IS NULL OR profile IS NULL OR sequence_no = 0"
    ))


def _replace_interval_constraint() -> None:
    connection = op.get_bind()
    if "review_tasks" not in table_names(connection):
        return
    unique_constraints = {
        item.get("name") for item in inspect(connection).get_unique_constraints("review_tasks")
    }
    dialect = connection.dialect.name
    old_present = "uq_review_task_interval" in unique_constraints
    new_present = "uq_review_task_schedule_sequence" in unique_constraints
    if dialect == "sqlite":
        # SQLite cannot drop a named unique constraint directly.  The current
        # test schema is metadata-created, but batch mode keeps upgrades safe
        # for older local SQLite databases too.
        with op.batch_alter_table("review_tasks") as batch:  # type: ignore[attr-defined]
            if old_present:
                batch.drop_constraint("uq_review_task_interval", type_="unique")
            if not new_present:
                batch.create_unique_constraint(
                    "uq_review_task_schedule_sequence",
                    ["mistake_id", "schedule_version", "sequence_no"],
                )
    elif dialect in {"postgresql", "mysql"}:
        if old_present:
            op.drop_constraint("uq_review_task_interval", "review_tasks", type_="unique")
        if not new_present:
            op.create_unique_constraint(
                "uq_review_task_schedule_sequence",
                "review_tasks",
                ["mistake_id", "schedule_version", "sequence_no"],
            )
    elif not old_present and not new_present:
        # The migration must not silently claim success on an unknown backend.
        raise RuntimeError(f"不支持为 {dialect} 校验 review_tasks 唯一约束")

    constraints_after = {
        item.get("name") for item in inspect(connection).get_unique_constraints("review_tasks")
    }
    if "uq_review_task_interval" in constraints_after:
        raise RuntimeError("迁移后旧的 uq_review_task_interval 约束仍存在")
    if "uq_review_task_schedule_sequence" not in constraints_after:
        raise RuntimeError("迁移后缺少 uq_review_task_schedule_sequence 唯一约束")


def upgrade() -> None:
    connection = op.get_bind()
    create_registered_schema(connection)
    add_missing_columns(connection)
    _backfill_review_rows()
    ensure_review_task_compatibility(connection)
    _replace_interval_constraint()
    ensure_registered_indexes(connection)


def downgrade() -> None:
    # Policy metadata and audit history are additive and are not destructively
    # removed on downgrade.
    pass
