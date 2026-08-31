"""Alembic migration primitives shared by revisions and legacy wrappers."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from typing import Any

from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Connection, Engine

from domain.learning.mastery import (
    derive_mastery,
    knowledge_point_id,
    normalize_knowledge_point_name,
)
from persistence.mistake_store import mistake_attributions, mistake_items
from persistence.schema import knowledge_points, lesson_publications, mastery_states
from persistence.schema_registry import (
    SCHEMA_HEAD_REVISION,
    iter_metadata,
    table_registry,
)

LEGACY_MASTERY_TABLE = "mastery_states_legacy"
LEGACY_PUBLICATION_ID = "legacy-mastery-v2"
_ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    "learning_sessions": {"assignment_id": "VARCHAR(64)"},
    "exercise_attempts": {
        "publication_id": "VARCHAR(128)",
        "knowledge_point_id": "VARCHAR(64)",
    },
    "assignments": {"assignment_plan_id": "VARCHAR(64)"},
    "mistake_items": {
        "error_reason": "VARCHAR(32)",
        "ai_error_reason": "VARCHAR(32)",
        "ai_error_reason_confidence": "FLOAT",
    },
    "variation_exercises": {
        "attribution_source": "VARCHAR(16) NOT NULL DEFAULT 'unknown'",
    },
    "review_tasks": {
        "evaluation_evidence_json": "JSON_OBJECT_NOT_NULL",
    },
}
_ADDITIVE_FOREIGN_KEYS: tuple[dict[str, Any], ...] = (
    {
        "name": "fk_learning_sessions_assignment",
        "table": "learning_sessions",
        "columns": ("assignment_id",),
        "referred_table": "assignments",
        "referred_columns": ("assignment_id",),
    },
    {
        "name": "fk_assignments_assignment_plan",
        "table": "assignments",
        "columns": ("assignment_plan_id",),
        "referred_table": "assignment_plans",
        "referred_columns": ("plan_id",),
    },
)


def table_names(connection: Connection) -> set[str]:
    return set(inspect(connection).get_table_names())


def column_names(connection: Connection, table_name: str) -> set[str]:
    if table_name not in table_names(connection):
        return set()
    return {column["name"] for column in inspect(connection).get_columns(table_name)}


def index_names(connection: Connection, table_name: str) -> set[str]:
    if table_name not in table_names(connection):
        return set()
    return {
        index["name"]
        for index in inspect(connection).get_indexes(table_name)
        if index["name"] is not None
    }


def quote_identifier(value: str) -> str:
    """Quote an identifier from a checked-in constant, never user input."""
    return '"' + value.replace('"', '""') + '"'


def create_registered_schema(
    connection: Connection,
    *,
    exclude_tables: set[str] | None = None,
) -> None:
    """Create missing tables from the registry without changing existing rows."""
    for schema, tables in iter_metadata(exclude_tables=exclude_tables or set()):
        schema.create_all(connection, tables=tables, checkfirst=True)


def add_missing_columns(connection: Connection) -> list[str]:
    """Add only known nullable/additive compatibility columns."""
    added: list[str] = []
    for table_name, columns in _ADDITIVE_COLUMNS.items():
        if table_name not in table_names(connection):
            continue
        existing = column_names(connection, table_name)
        for column_name, definition in columns.items():
            if column_name in existing:
                continue
            if definition == "JSON_OBJECT_NOT_NULL":
                definition = (
                    "JSONB NOT NULL DEFAULT '{}'::jsonb"
                    if connection.dialect.name == "postgresql"
                    else "JSON NOT NULL DEFAULT '{}'"
                )
            connection.execute(
                text(
                    f"ALTER TABLE {quote_identifier(table_name)} "
                    f"ADD COLUMN {quote_identifier(column_name)} {definition}"
                )
            )
            added.append(f"{table_name}.{column_name}")
            existing.add(column_name)
    return added


def _foreign_key_matches(reflected: Mapping[str, Any], expected: dict[str, Any]) -> bool:
    return (
        tuple(reflected.get("constrained_columns") or ()) == expected["columns"]
        and reflected.get("referred_table") == expected["referred_table"]
        and tuple(reflected.get("referred_columns") or ()) == expected["referred_columns"]
    )


def missing_foreign_keys(connection: Connection) -> list[str]:
    """Return missing assignment FK names without changing the database."""
    tables = table_names(connection)
    missing: list[str] = []
    inspector = inspect(connection)
    for expected in _ADDITIVE_FOREIGN_KEYS:
        if expected["table"] not in tables or expected["referred_table"] not in tables:
            continue
        if not set(expected["columns"]).issubset(column_names(connection, expected["table"])):
            continue
        reflected = inspector.get_foreign_keys(expected["table"])
        if not any(_foreign_key_matches(item, expected) for item in reflected):
            missing.append(expected["name"])
    return missing


def foreign_key_orphan_counts(connection: Connection) -> dict[str, int]:
    """Count non-null child values that would violate the assignment FKs."""
    tables = table_names(connection)
    counts: dict[str, int] = {}
    for expected in _ADDITIVE_FOREIGN_KEYS:
        if expected["table"] not in tables or expected["referred_table"] not in tables:
            continue
        if not set(expected["columns"]).issubset(column_names(connection, expected["table"])):
            continue
        child = quote_identifier(expected["table"])
        child_column = quote_identifier(expected["columns"][0])
        parent = quote_identifier(expected["referred_table"])
        parent_column = quote_identifier(expected["referred_columns"][0])
        count = connection.scalar(
            text(
                f"SELECT COUNT(*) FROM {child} AS child "
                f"WHERE child.{child_column} IS NOT NULL "
                f"AND NOT EXISTS (SELECT 1 FROM {parent} AS parent "
                f"WHERE parent.{parent_column} = child.{child_column})"
            )
        )
        counts[expected["name"]] = int(count or 0)
    return counts


def ensure_registered_foreign_keys(connection: Connection) -> list[str]:
    """Add the new assignment FKs on PostgreSQL after an orphan safety check.

    SQLite cannot add a foreign key to an existing table without rebuilding it;
    its fresh schema is created with the constraints by the registry, while a
    partial legacy SQLite database remains explicitly not-ready in reports.
    """
    if connection.dialect.name != "postgresql":
        return []
    tables = table_names(connection)
    inspector = inspect(connection)
    pending: list[dict[str, Any]] = []
    for expected in _ADDITIVE_FOREIGN_KEYS:
        if expected["table"] not in tables or expected["referred_table"] not in tables:
            continue
        if not set(expected["columns"]).issubset(column_names(connection, expected["table"])):
            continue
        reflected = inspector.get_foreign_keys(expected["table"])
        if any(_foreign_key_matches(item, expected) for item in reflected):
            continue
        if any(item.get("name") == expected["name"] for item in reflected):
            raise RuntimeError(
                f"外键 {expected['name']} 已存在但定义不一致，拒绝覆盖"
            )
        orphan_count = foreign_key_orphan_counts(connection).get(expected["name"], 0)
        if orphan_count:
            raise RuntimeError(
                f"拒绝添加外键 {expected['name']}：发现 {orphan_count} 条孤儿引用，"
                "请先人工修复数据；迁移不会删除或改写这些行"
            )
        pending.append(expected)
    added: list[str] = []
    for expected in pending:
        columns = ", ".join(quote_identifier(column) for column in expected["columns"])
        referred_columns = ", ".join(
            quote_identifier(column) for column in expected["referred_columns"]
        )
        connection.execute(
            text(
                f"ALTER TABLE {quote_identifier(expected['table'])} "
                f"ADD CONSTRAINT {quote_identifier(expected['name'])} "
                f"FOREIGN KEY ({columns}) REFERENCES "
                f"{quote_identifier(expected['referred_table'])} ({referred_columns})"
            )
        )
        added.append(expected["name"])
    return added


def ensure_registered_indexes(connection: Connection) -> list[str]:
    """Create missing named indexes after additive columns are available."""
    created: list[str] = []
    for schema, tables in iter_metadata():
        del schema
        for table in tables:
            if table.name not in table_names(connection):
                continue
            existing = index_names(connection, table.name)
            for index in table.indexes:
                if index.name in existing:
                    continue
                if any(column.name not in column_names(connection, table.name) for column in index.columns):
                    # A genuinely partial legacy table may still need an
                    # additive column revision before its index can be built.
                    continue
                index.create(connection, checkfirst=True)
                created.append(index.name or "")
                existing.add(index.name or "")
    return created


def ensure_current_schema(connection: Connection, *, include_attributions: bool = True) -> dict[str, list[str]]:
    """Create missing tables and additive columns for an adoption revision."""
    exclude = set() if include_attributions else {"mistake_attributions"}
    create_registered_schema(connection, exclude_tables=exclude)
    added_columns = add_missing_columns(connection)
    added_foreign_keys = ensure_registered_foreign_keys(connection)
    created_indexes = ensure_registered_indexes(connection)
    return {
        "addedColumns": added_columns,
        "addedForeignKeys": added_foreign_keys,
        "createdIndexes": created_indexes,
    }


def mastery_primary_key(connection: Connection, table_name: str = "mastery_states") -> list[str]:
    if table_name not in table_names(connection):
        return []
    return list(inspect(connection).get_pk_constraint(table_name).get("constrained_columns") or [])


def needs_mastery_rebuild(connection: Connection) -> bool:
    return "mastery_states" in table_names(connection) and mastery_primary_key(connection) != [
        "learner_id",
        "knowledge_point_id",
    ]


def mastery_plan(connection: Connection) -> dict[str, Any]:
    tables = table_names(connection)
    expected_columns = {
        "exercise_attempts": {"publication_id", "knowledge_point_id"},
        "mastery_states": {
            "knowledge_point_id",
            "raw_score",
            "evidence_confidence",
            "evidence_count",
            "algorithm_version",
            "computed_at",
        },
    }
    missing_columns = {
        table: sorted(expected - column_names(connection, table))
        for table, expected in expected_columns.items()
        if table in tables
    }
    return {
        "missingTables": sorted({"knowledge_points", "mastery_states"} - tables),
        "missingColumns": {table: values for table, values in missing_columns.items() if values},
        "missingIndexes": sorted(
            {"uq_mastery_states_learner_knowledge_point"}
            - index_names(connection, "mastery_states")
            if "mastery_states" in tables
            else set()
        ),
        "masteryPrimaryKey": mastery_primary_key(connection),
        "masteryRebuildRequired": needs_mastery_rebuild(connection),
    }


def preserve_legacy_mastery(connection: Connection) -> bool:
    """Rename an old projection without dropping its rows."""
    if not needs_mastery_rebuild(connection):
        return False
    if LEGACY_MASTERY_TABLE in table_names(connection):
        raise RuntimeError(
            "同时存在旧 mastery_states 和 mastery_states_legacy，拒绝覆盖；请检查上次迁移状态"
        )
    if "uq_mastery_states_learner_knowledge_point" in index_names(connection, "mastery_states"):
        connection.execute(text("DROP INDEX \"uq_mastery_states_learner_knowledge_point\""))
    connection.execute(text("ALTER TABLE \"mastery_states\" RENAME TO \"mastery_states_legacy\""))
    return True


def _ensure_legacy_publication(connection: Connection) -> None:
    if connection.scalar(
        select(lesson_publications.c.publication_id).where(
            lesson_publications.c.publication_id == LEGACY_PUBLICATION_ID
        )
    ):
        return
    now = time.time()
    connection.execute(
        lesson_publications.insert().values(
            publication_id=LEGACY_PUBLICATION_ID,
            title="Legacy mastery import",
            source_upload_id=None,
            lesson_ids_json=[],
            status="archived",
            version=1,
            revision_of=None,
            created_at=now,
            updated_at=now,
        )
    )


def _ensure_knowledge_point(connection: Connection, name: str) -> str:
    normalized = normalize_knowledge_point_name(name)
    point_id = knowledge_point_id(LEGACY_PUBLICATION_ID, normalized)
    if not connection.scalar(
        select(knowledge_points.c.knowledge_point_id).where(
            knowledge_points.c.knowledge_point_id == point_id
        )
    ):
        connection.execute(
            knowledge_points.insert().values(
                knowledge_point_id=point_id,
                publication_id=LEGACY_PUBLICATION_ID,
                name=normalized,
                normalized_name=normalized,
                created_at=time.time(),
            )
        )
    return point_id


def backfill_mastery(connection: Connection, *, apply: bool = True) -> dict[str, int]:
    """Backfill stable knowledge-point IDs and rebuild the v2 projection."""
    tables = table_names(connection)
    if "exercise_attempts" not in tables or "learning_sessions" not in tables:
        attempts: Any = []
    else:
        attempts = connection.execute(
            text(
                "SELECT a.attempt_id, s.publication_id, a.knowledge_point "
                "FROM exercise_attempts a JOIN learning_sessions s "
                "ON s.session_id = a.session_id "
                "WHERE a.knowledge_point IS NOT NULL AND a.knowledge_point_id IS NULL"
            )
        ).mappings().all()
    mastery = (
        connection.execute(
            text(
                "SELECT learner_id, knowledge_point, score, attempt_count, correct_count, "
                "last_practiced_at FROM mastery_states_legacy "
                "WHERE knowledge_point IS NOT NULL"
            )
        ).mappings().all()
        if LEGACY_MASTERY_TABLE in tables
        else []
    )
    if not apply:
        return {"attemptsUpdated": len(attempts), "masteryUpdated": len(mastery)}

    for row in attempts:
        name = normalize_knowledge_point_name(row["knowledge_point"])
        publication_exists = connection.scalar(
            select(lesson_publications.c.publication_id).where(
                lesson_publications.c.publication_id == row["publication_id"]
            )
        )
        if publication_exists:
            point_id = knowledge_point_id(row["publication_id"], name)
            if not connection.scalar(
                select(knowledge_points.c.knowledge_point_id).where(
                    knowledge_points.c.knowledge_point_id == point_id
                )
            ):
                connection.execute(
                    knowledge_points.insert().values(
                        knowledge_point_id=point_id,
                        publication_id=row["publication_id"],
                        name=name,
                        normalized_name=name,
                        created_at=time.time(),
                    )
                )
        else:
            _ensure_legacy_publication(connection)
            point_id = _ensure_knowledge_point(connection, name)
        connection.execute(
            text(
                "UPDATE exercise_attempts SET publication_id = :publication_id, "
                "knowledge_point_id = :knowledge_point_id WHERE attempt_id = :attempt_id"
            ),
            {
                "publication_id": row["publication_id"],
                "knowledge_point_id": point_id,
                "attempt_id": row["attempt_id"],
            },
        )

    evidence_rows = (
        connection.execute(
            text(
                "SELECT s.learner_id, a.publication_id, a.question_id, a.attempt_id, "
                "a.assessment, a.created_at, a.knowledge_point_id, a.knowledge_point "
                "FROM exercise_attempts a JOIN learning_sessions s "
                "ON s.session_id = a.session_id "
                "WHERE a.knowledge_point_id IS NOT NULL"
            )
        ).mappings().all()
        if "exercise_attempts" in tables and "learning_sessions" in tables
        else []
    )
    evidence_by_state: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for evidence in evidence_rows:
        key = (evidence["learner_id"], evidence["knowledge_point_id"])
        evidence_by_state.setdefault(key, []).append(dict(evidence))
    evidence_state_by_name = {
        (learner_id, normalize_knowledge_point_name(evidence[0]["knowledge_point"])): point_id
        for (learner_id, point_id), evidence in evidence_by_state.items()
        if evidence and evidence[0].get("knowledge_point")
    }
    rebuilt = 0
    for (learner_id, point_id), evidence in evidence_by_state.items():
        if connection.scalar(
            select(mastery_states.c.learner_id).where(
                mastery_states.c.learner_id == learner_id,
                mastery_states.c.knowledge_point_id == point_id,
            )
        ):
            continue
        derived = derive_mastery(evidence)
        name = connection.scalar(
            select(knowledge_points.c.name).where(
                knowledge_points.c.knowledge_point_id == point_id
            )
        ) or "未分类知识点"
        connection.execute(
            mastery_states.insert().values(
                learner_id=learner_id,
                knowledge_point_id=point_id,
                knowledge_point=name,
                score=derived["score"],
                raw_score=derived["raw_score"],
                evidence_confidence=derived["evidence_confidence"],
                evidence_count=derived["evidence_count"],
                algorithm_version=derived["algorithm_version"],
                computed_at=time.time(),
                attempt_count=derived["attempt_count"],
                correct_count=derived["correct_count"],
                last_practiced_at=derived["last_practiced_at"],
            )
        )
        rebuilt += 1

    preserved = 0
    for row in mastery:
        name = normalize_knowledge_point_name(row["knowledge_point"])
        if (row["learner_id"], name) in evidence_state_by_name:
            continue
        _ensure_legacy_publication(connection)
        point_id = _ensure_knowledge_point(connection, name)
        if connection.scalar(
            select(mastery_states.c.learner_id).where(
                mastery_states.c.learner_id == row["learner_id"],
                mastery_states.c.knowledge_point_id == point_id,
            )
        ):
            continue
        connection.execute(
            mastery_states.insert().values(
                learner_id=row["learner_id"],
                knowledge_point_id=point_id,
                knowledge_point=name,
                score=row["score"],
                raw_score=row["score"],
                evidence_confidence=0,
                evidence_count=row["attempt_count"],
                algorithm_version="mastery-v1-legacy",
                computed_at=row["last_practiced_at"] or time.time(),
                attempt_count=row["attempt_count"],
                correct_count=row["correct_count"],
                last_practiced_at=row["last_practiced_at"],
            )
        )
        preserved += 1
    return {"attemptsUpdated": len(attempts), "masteryUpdated": rebuilt + preserved}


def upgrade_mastery(connection: Connection) -> dict[str, Any]:
    rebuilt = preserve_legacy_mastery(connection)
    ensure_current_schema(connection, include_attributions=False)
    backfill = backfill_mastery(connection)
    return {"masteryRebuilt": rebuilt, "backfill": backfill}


def variation_plan(connection: Connection) -> dict[str, Any]:
    tables = table_names(connection)
    columns = column_names(connection, "variation_exercises") if "variation_exercises" in tables else set()
    return {
        "missingTables": sorted({"variation_exercises"} - tables),
        "missingColumns": {"variation_exercises": ["attribution_source"]}
        if "variation_exercises" in tables and "attribution_source" not in columns
        else {},
    }


def upgrade_variation(connection: Connection) -> dict[str, Any]:
    before = variation_plan(connection)
    ensure_current_schema(connection, include_attributions=False)
    if "variation_exercises" in table_names(connection):
        connection.execute(
            text(
                "UPDATE variation_exercises SET attribution_source = 'unknown' "
                "WHERE attribution_source IS NULL OR attribution_source NOT IN ('ai', 'self', 'unknown')"
            )
        )
    return {"before": before}


def assignment_plan(connection: Connection) -> dict[str, Any]:
    tables = table_names(connection)
    missing_columns = {}
    if "learning_sessions" in tables and "assignment_id" not in column_names(connection, "learning_sessions"):
        missing_columns["learning_sessions"] = ["assignment_id"]
    if "assignments" in tables and "assignment_plan_id" not in column_names(connection, "assignments"):
        missing_columns["assignments"] = ["assignment_plan_id"]
    return {
        "missingTables": sorted({"learning_classes", "class_memberships", "assignments", "assignment_plans"} - tables),
        "missingColumns": missing_columns,
        "missingIndexes": sorted(
            {"idx_learning_sessions_assignment"}
            - index_names(connection, "learning_sessions")
            if "learning_sessions" in tables
            else set()
        ) + sorted(
            {"idx_assignments_plan"}
            - index_names(connection, "assignments")
            if "assignments" in tables
            else set()
        ) + sorted(
            {"idx_assignment_plans_class"}
            - index_names(connection, "assignment_plans")
            if "assignment_plans" in tables
            else set()
        ),
    }


def upgrade_assignments(connection: Connection) -> dict[str, Any]:
    before = assignment_plan(connection)
    ensure_current_schema(connection, include_attributions=False)
    return {"before": before}


def teacher_review_plan(connection: Connection) -> dict[str, Any]:
    return {"missingTables": sorted({"teacher_review_events"} - table_names(connection))}


def upgrade_teacher_review(connection: Connection) -> dict[str, Any]:
    before = teacher_review_plan(connection)
    ensure_current_schema(connection, include_attributions=False)
    return {"before": before}


def _legacy_attribution_id(mistake_id: str, source: str, category: str) -> str:
    raw = f"legacy-column:{mistake_id}:{source}:{category}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def backfill_mistake_attributions(connection: Connection) -> dict[str, int]:
    """Copy legacy columns once, using deterministic IDs for idempotency."""
    if "mistake_items" not in table_names(connection) or "mistake_attributions" not in table_names(connection):
        return {"self": 0, "ai": 0}
    rows = connection.execute(
        select(
            mistake_items.c.mistake_id,
            mistake_items.c.error_reason,
            mistake_items.c.ai_error_reason,
            mistake_items.c.ai_error_reason_confidence,
            mistake_items.c.created_at,
            mistake_items.c.confirmed_at,
        )
    ).mappings().all()
    counts = {"self": 0, "ai": 0}
    for row in rows:
        values = (
            ("self", row["error_reason"], 1.0, "error_reason"),
            (
                "ai",
                row["ai_error_reason"],
                row["ai_error_reason_confidence"]
                if row["ai_error_reason_confidence"] is not None
                else 0.0,
                "ai_error_reason",
            ),
        )
        for source, category, confidence, field in values:
            if not category or category == "unknown":
                continue
            attribution_id = _legacy_attribution_id(row["mistake_id"], source, category)
            if connection.scalar(
                select(mistake_attributions.c.attribution_id).where(
                    mistake_attributions.c.attribution_id == attribution_id
                )
            ):
                continue
            connection.execute(
                mistake_attributions.insert().values(
                    attribution_id=attribution_id,
                    mistake_id=row["mistake_id"],
                    source=source,
                    category=category,
                    confidence=max(0.0, min(1.0, float(confidence))),
                    evidence_json={"migration": "legacy-column", "field": field},
                    model_version=None,
                    created_at=row["confirmed_at"] or row["created_at"],
                    accepted_at=row["confirmed_at"],
                )
            )
            counts[source] += 1
    return counts


def attribution_plan(connection: Connection) -> dict[str, Any]:
    return {
        "missingTables": sorted({"mistake_attributions"} - table_names(connection)),
        "missingColumns": {},
    }


def upgrade_mistake_attributions(connection: Connection) -> dict[str, Any]:
    before = attribution_plan(connection)
    create_registered_schema(connection)
    add_missing_columns(connection)
    backfill = backfill_mistake_attributions(connection)
    return {"before": before, "backfill": backfill}


def schema_report(engine: Engine, *, require_version: bool = False) -> dict[str, Any]:
    """Return a sanitized readiness report without exposing connection details."""
    with engine.connect() as connection:
        tables = table_names(connection)
        registry = table_registry()
        missing_tables = sorted(set(registry) - tables)
        missing_columns: dict[str, list[str]] = {}
        missing_indexes: list[str] = []
        for table_name, table in registry.items():
            if table_name not in tables:
                continue
            missing = sorted({column.name for column in table.columns} - column_names(connection, table_name))
            if missing:
                missing_columns[table_name] = missing
            existing_indexes = index_names(connection, table_name)
            missing_indexes.extend(
                index.name for index in table.indexes if index.name and index.name not in existing_indexes
            )
        version = None
        if "alembic_version" in tables:
            version = connection.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
        version_state = "current" if version == SCHEMA_HEAD_REVISION else "missing"
        if version is not None and version != SCHEMA_HEAD_REVISION:
            version_state = "outdated"
        missing_fk_names = missing_foreign_keys(connection)
        orphan_counts = foreign_key_orphan_counts(connection)
        auto_fixable_columns = sorted(
            f"{table_name}.{column_name}"
            for table_name, columns in missing_columns.items()
            for column_name in columns
            if column_name in _ADDITIVE_COLUMNS.get(table_name, {})
        )
        auto_fixable_indexes = sorted(
            index_name
            for table_name, table in registry.items()
            if table_name in tables
            for index in table.indexes
            for index_name in [index.name]
            if index_name in missing_indexes
            and all(column.name in column_names(connection, table_name) for column in index.columns)
        )
        auto_fixable_foreign_keys = sorted(
            name
            for name in missing_fk_names
            if engine.dialect.name == "postgresql" and orphan_counts.get(name, 0) == 0
        )
        manual_foreign_keys = sorted(set(missing_fk_names) - set(auto_fixable_foreign_keys))
        manual_columns = {
            table_name: [
                column_name
                for column_name in columns
                if column_name not in _ADDITIVE_COLUMNS.get(table_name, {})
            ]
            for table_name, columns in missing_columns.items()
        }
        manual_columns = {
            table_name: columns
            for table_name, columns in manual_columns.items()
            if columns
        }
        auto_fixable = {
            "tables": missing_tables,
            "columns": auto_fixable_columns,
            "indexes": auto_fixable_indexes,
            "foreignKeys": auto_fixable_foreign_keys,
        }
        manual_action_required = {
            "columns": manual_columns,
            "foreignKeys": manual_foreign_keys,
            "orphanCounts": {
                name: count for name, count in orphan_counts.items() if count
            },
        }
        ready = (
            not missing_tables
            and not missing_columns
            and not missing_indexes
            and not missing_fk_names
            and not any(orphan_counts.values())
        )
        if require_version and version != SCHEMA_HEAD_REVISION:
            ready = False
        return {
            "backend": engine.dialect.name,
            "ready": ready,
            "version": version,
            "head": SCHEMA_HEAD_REVISION,
            "versionState": version_state,
            "missingTables": missing_tables,
            "missingColumns": missing_columns,
            "missingIndexes": sorted(set(missing_indexes)),
            "missingForeignKeys": missing_fk_names,
            "orphanCounts": orphan_counts,
            "autoFixable": auto_fixable,
            "manualActionRequired": manual_action_required,
        }


def schema_is_ready(engine: Engine, *, require_version: bool = False) -> bool:
    return bool(schema_report(engine, require_version=require_version)["ready"])
