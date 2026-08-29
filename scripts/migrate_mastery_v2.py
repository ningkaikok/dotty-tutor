"""Migrate the legacy learning projection to mastery-v2.

The migration preserves legacy rows in ``mastery_states_legacy`` when the old
table uses ``(learner_id, knowledge_point)`` as its primary key. It then
creates the real v2 table, backfills stable knowledge-point entities, and is
safe to repeat. Run with ``--dry-run`` first, then ``--apply`` and ``--verify``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text

# The script is documented and invoked from the repository root. Keep the
# application import path explicit so it does not depend on PYTHONPATH.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPOSITORY_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from domain.learning.mastery import (
    derive_mastery,
    knowledge_point_id,
    normalize_knowledge_point_name,
)
from persistence.database import build_postgres_url_from_env, normalize_database_url
from persistence.schema import metadata

LEGACY_MASTERY_TABLE = "mastery_states_legacy"
LEGACY_PUBLICATION_ID = "legacy-mastery-v2"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--data-root", default=os.getenv("DOTTY_DATA_DIR"))
    return parser.parse_args()


def database_url(args: argparse.Namespace) -> str:
    if args.database_url:
        return normalize_database_url(args.database_url)
    if args.data_root:
        return f"sqlite+pysqlite:///{Path(args.data_root).expanduser().resolve() / 'dotty.sqlite3'}"
    return normalize_database_url(build_postgres_url_from_env())


def table_names(connection: Any) -> set[str]:
    return set(inspect(connection).get_table_names())


def columns(connection: Any, table_name: str) -> set[str]:
    return {column["name"] for column in inspect(connection).get_columns(table_name)}


def mastery_primary_key(connection: Any, table_name: str = "mastery_states") -> list[str]:
    if table_name not in table_names(connection):
        return []
    return list(inspect(connection).get_pk_constraint(table_name).get("constrained_columns") or [])


def needs_mastery_rebuild(connection: Any) -> bool:
    return "mastery_states" in table_names(connection) and mastery_primary_key(connection) != [
        "learner_id", "knowledge_point_id"
    ]


def migration_plan(connection: Any) -> dict[str, Any]:
    tables = table_names(connection)
    expected_columns = {
        "exercise_attempts": {"publication_id", "knowledge_point_id"},
        "mastery_states": {
            "knowledge_point_id", "raw_score", "evidence_confidence", "evidence_count",
            "algorithm_version", "computed_at",
        },
    }
    missing_tables = sorted({"knowledge_points", "mastery_states"} - tables)
    missing_columns = {
        table: sorted(expected - columns(connection, table))
        for table, expected in expected_columns.items()
        if table in tables
    }
    indexes = (
        {item["name"] for item in inspect(connection).get_indexes("mastery_states")}
        if "mastery_states" in tables
        else set()
    )
    missing_indexes = []
    if "mastery_states" in tables and "uq_mastery_states_learner_knowledge_point" not in indexes:
        missing_indexes.append("uq_mastery_states_learner_knowledge_point")
    return {
        "missingTables": missing_tables,
        "missingColumns": {table: values for table, values in missing_columns.items() if values},
        "missingIndexes": missing_indexes,
        "masteryPrimaryKey": mastery_primary_key(connection),
        "masteryRebuildRequired": needs_mastery_rebuild(connection),
    }


def preserve_legacy_mastery(connection: Any) -> bool:
    """Rename the old table once; this keeps its original rows and primary key."""
    if not needs_mastery_rebuild(connection):
        return False
    if LEGACY_MASTERY_TABLE in table_names(connection):
        raise RuntimeError(
            "同时存在旧 mastery_states 和 mastery_states_legacy，拒绝覆盖；请检查上次迁移状态"
        )
    index_names = {item["name"] for item in inspect(connection).get_indexes("mastery_states")}
    if "uq_mastery_states_learner_knowledge_point" in index_names:
        connection.execute(text("DROP INDEX uq_mastery_states_learner_knowledge_point"))
    connection.execute(text(f"ALTER TABLE mastery_states RENAME TO {LEGACY_MASTERY_TABLE}"))
    return True


def apply_schema(connection: Any) -> bool:
    rebuilt = preserve_legacy_mastery(connection)
    metadata.create_all(connection)
    current_columns = columns(connection, "exercise_attempts")
    if "publication_id" not in current_columns:
        connection.execute(text(
            "ALTER TABLE exercise_attempts ADD COLUMN publication_id VARCHAR(128)"
        ))
    if "knowledge_point_id" not in current_columns:
        connection.execute(text(
            "ALTER TABLE exercise_attempts ADD COLUMN knowledge_point_id VARCHAR(64)"
        ))
    connection.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_mastery_states_learner_knowledge_point "
        "ON mastery_states (learner_id, knowledge_point_id)"
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_exercise_attempts_publication_question "
        "ON exercise_attempts (publication_id, question_id, created_at)"
    ))
    return rebuilt


def ensure_legacy_publication(connection: Any) -> None:
    exists = connection.scalar(text(
        "SELECT 1 FROM lesson_publications WHERE publication_id = :publication_id"
    ), {"publication_id": LEGACY_PUBLICATION_ID})
    if exists:
        return
    now = time.time()
    connection.execute(text(
        "INSERT INTO lesson_publications "
        "(publication_id, title, source_upload_id, lesson_ids_json, status, version, revision_of, created_at, updated_at) "
        "VALUES (:publication_id, :title, NULL, :lesson_ids_json, 'archived', 1, NULL, :created_at, :updated_at)"
    ), {
        "publication_id": LEGACY_PUBLICATION_ID,
        "title": "Legacy mastery import",
        "lesson_ids_json": "[]",
        "created_at": now,
        "updated_at": now,
    })


def ensure_knowledge_point(connection: Any, name: str) -> str:
    normalized = normalize_knowledge_point_name(name)
    point_id = knowledge_point_id(LEGACY_PUBLICATION_ID, normalized)
    exists = connection.scalar(text(
        "SELECT 1 FROM knowledge_points WHERE knowledge_point_id = :knowledge_point_id"
    ), {"knowledge_point_id": point_id})
    if exists is None:
        connection.execute(text(
            "INSERT INTO knowledge_points "
            "(knowledge_point_id, publication_id, name, normalized_name, created_at) "
            "VALUES (:knowledge_point_id, :publication_id, :name, :normalized_name, :created_at)"
        ), {
            "knowledge_point_id": point_id,
            "publication_id": LEGACY_PUBLICATION_ID,
            "name": normalized,
            "normalized_name": normalized,
            "created_at": time.time(),
        })
    return point_id


def backfill(connection: Any, *, apply: bool) -> dict[str, int]:
    """Backfill IDs and rebuild projections from immutable attempt evidence."""
    tables = table_names(connection)
    attempt_columns = columns(connection, "exercise_attempts") if "exercise_attempts" in tables else set()
    has_knowledge_point_id = "knowledge_point_id" in attempt_columns
    attempts = connection.execute(text(
        "SELECT a.attempt_id, s.publication_id, a.knowledge_point "
        "FROM exercise_attempts a JOIN learning_sessions s ON s.session_id = a.session_id "
        "WHERE a.knowledge_point IS NOT NULL"
        + (" AND a.knowledge_point_id IS NULL" if has_knowledge_point_id else "")
    )).mappings().all() if "exercise_attempts" in tables else []
    source_table = LEGACY_MASTERY_TABLE if LEGACY_MASTERY_TABLE in tables else None
    mastery = connection.execute(text(
        f"SELECT learner_id, knowledge_point, score, attempt_count, correct_count, last_practiced_at "
        f"FROM {source_table} WHERE knowledge_point IS NOT NULL"
    )).mappings().all() if source_table else []
    if not apply:
        return {"attemptsUpdated": len(attempts), "masteryUpdated": len(mastery)}

    for row in attempts:
        name = normalize_knowledge_point_name(row["knowledge_point"])
        publication_exists = connection.scalar(text(
            "SELECT 1 FROM lesson_publications WHERE publication_id = :publication_id"
        ), {"publication_id": row["publication_id"]})
        if publication_exists:
            point_id = knowledge_point_id(row["publication_id"], name)
            exists = connection.scalar(text(
                "SELECT 1 FROM knowledge_points WHERE knowledge_point_id = :knowledge_point_id"
            ), {"knowledge_point_id": point_id})
            if exists is None:
                connection.execute(text(
                    "INSERT INTO knowledge_points "
                    "(knowledge_point_id, publication_id, name, normalized_name, created_at) "
                    "VALUES (:knowledge_point_id, :publication_id, :name, :normalized_name, :created_at)"
                ), {
                    "knowledge_point_id": point_id,
                    "publication_id": row["publication_id"],
                    "name": name,
                    "normalized_name": name,
                    "created_at": time.time(),
                })
        else:
            ensure_legacy_publication(connection)
            point_id = ensure_knowledge_point(connection, name)
        connection.execute(text(
            "UPDATE exercise_attempts SET publication_id = :publication_id, "
            "knowledge_point_id = :knowledge_point_id WHERE attempt_id = :attempt_id"
        ), {
            "publication_id": row["publication_id"],
            "knowledge_point_id": point_id,
            "attempt_id": row["attempt_id"],
        })

    attempt_evidence = connection.execute(text(
        "SELECT s.learner_id, a.publication_id, a.question_id, a.attempt_id, "
        "a.assessment, a.created_at, a.knowledge_point_id, a.knowledge_point "
        "FROM exercise_attempts a "
        "JOIN learning_sessions s ON s.session_id = a.session_id "
        "WHERE a.knowledge_point_id IS NOT NULL"
    )).mappings().all() if "exercise_attempts" in tables and has_knowledge_point_id else []
    evidence_by_state: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for evidence in attempt_evidence:
        key = (evidence["learner_id"], evidence["knowledge_point_id"])
        evidence_by_state.setdefault(key, []).append(dict(evidence))
    evidence_state_by_name = {
        (learner_id, normalize_knowledge_point_name(evidence[0]["knowledge_point"])): point_id
        for (learner_id, point_id), evidence in evidence_by_state.items()
        if evidence and evidence[0].get("knowledge_point")
    }

    rebuilt = 0
    for (learner_id, point_id), evidence in evidence_by_state.items():
        already_moved = connection.scalar(text(
            "SELECT 1 FROM mastery_states WHERE learner_id = :learner_id "
            "AND knowledge_point_id = :knowledge_point_id"
        ), {"learner_id": learner_id, "knowledge_point_id": point_id})
        if already_moved:
            continue
        derived = derive_mastery(evidence)
        name = connection.scalar(text(
            "SELECT name FROM knowledge_points WHERE knowledge_point_id = :knowledge_point_id"
        ), {"knowledge_point_id": point_id}) or "未分类知识点"
        computed_at = time.time()
        connection.execute(text(
            "INSERT INTO mastery_states "
            "(learner_id, knowledge_point_id, knowledge_point, score, raw_score, evidence_confidence, "
            "evidence_count, algorithm_version, computed_at, attempt_count, correct_count, last_practiced_at) "
            "VALUES (:learner_id, :knowledge_point_id, :knowledge_point, :score, :raw_score, "
            ":evidence_confidence, :evidence_count, :algorithm_version, :computed_at, :attempt_count, "
            ":correct_count, :last_practiced_at)"
        ), {
            "learner_id": learner_id,
            "knowledge_point_id": point_id,
            "knowledge_point": name,
            "score": derived["score"],
            "raw_score": derived["raw_score"],
            "evidence_confidence": derived["evidence_confidence"],
            "evidence_count": derived["evidence_count"],
            "algorithm_version": derived["algorithm_version"],
            "computed_at": computed_at,
            "attempt_count": derived["attempt_count"],
            "correct_count": derived["correct_count"],
            "last_practiced_at": derived["last_practiced_at"],
        })
        rebuilt += 1

    # Preserve rows that have no immutable attempt evidence. They remain
    # explicitly marked as legacy instead of pretending the old EMA is v2.
    preserved = 0
    for row in mastery:
        name = normalize_knowledge_point_name(row["knowledge_point"])
        if (row["learner_id"], name) in evidence_state_by_name:
            continue
        ensure_legacy_publication(connection)
        point_id = ensure_knowledge_point(connection, name)
        already_moved = connection.scalar(text(
            "SELECT 1 FROM mastery_states WHERE learner_id = :learner_id "
            "AND knowledge_point_id = :knowledge_point_id"
        ), {"learner_id": row["learner_id"], "knowledge_point_id": point_id})
        if already_moved:
            continue
        connection.execute(text(
            "INSERT INTO mastery_states "
            "(learner_id, knowledge_point_id, knowledge_point, score, raw_score, evidence_confidence, "
            "evidence_count, algorithm_version, computed_at, attempt_count, correct_count, last_practiced_at) "
            "VALUES (:learner_id, :knowledge_point_id, :knowledge_point, :score, :raw_score, 0, "
            ":evidence_count, 'mastery-v1-legacy', :computed_at, :attempt_count, :correct_count, :last_practiced_at)"
        ), {
            "learner_id": row["learner_id"],
            "knowledge_point_id": point_id,
            "knowledge_point": name,
            "score": row["score"],
            "raw_score": row["score"],
            "evidence_count": row["attempt_count"],
            "computed_at": row["last_practiced_at"] or time.time(),
            "attempt_count": row["attempt_count"],
            "correct_count": row["correct_count"],
            "last_practiced_at": row["last_practiced_at"],
        })
        preserved += 1
    return {"attemptsUpdated": len(attempts), "masteryUpdated": rebuilt + preserved}


def verify(connection: Any) -> dict[str, Any]:
    plan = migration_plan(connection)
    tables = table_names(connection)
    null_attempts = connection.scalar(text(
        "SELECT COUNT(*) FROM exercise_attempts WHERE knowledge_point_id IS NULL"
    )) if "exercise_attempts" in tables else 0
    null_mastery = connection.scalar(text(
        "SELECT COUNT(*) FROM mastery_states WHERE knowledge_point_id IS NULL"
    )) if "mastery_states" in tables else 0
    return {
        "ready": (
            not plan["missingTables"]
            and not plan["missingColumns"]
            and not plan["missingIndexes"]
            and not plan["masteryRebuildRequired"]
            and plan["masteryPrimaryKey"] == ["learner_id", "knowledge_point_id"]
            and not null_attempts
            and not null_mastery
        ),
        "plan": plan,
        "nullKnowledgePointIds": {
            "exerciseAttempts": null_attempts or 0,
            "masteryStates": null_mastery or 0,
        },
        "legacyTablePreserved": LEGACY_MASTERY_TABLE in tables,
    }


def main() -> int:
    args = parse_args()
    engine = create_engine(database_url(args), future=True)
    if args.dry_run:
        with engine.connect() as connection:
            result = {
                "mode": "dry-run",
                "plan": migration_plan(connection),
                "backfill": backfill(connection, apply=False),
            }
    elif args.apply:
        with engine.begin() as connection:
            rebuilt = apply_schema(connection)
            result = {
                "mode": "apply",
                "masteryRebuilt": rebuilt,
                "backfill": backfill(connection, apply=True),
                "verify": verify(connection),
            }
    else:
        with engine.connect() as connection:
            result = {"mode": "verify", **verify(connection)}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ready", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
