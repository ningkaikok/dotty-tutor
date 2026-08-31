"""Deprecated compatibility wrapper for the Alembic mastery-v2 migration.

Use ``python -m persistence.migration_cli`` with ``preflight``, ``upgrade`` or
``verify`` from ``apps/api``. The implementation lives in ``persistence.migration_support``
so the compatibility command and the formal Alembic chain cannot diverge.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPOSITORY_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from persistence.database import build_postgres_url_from_env, normalize_database_url  # noqa: E402
from persistence.migration_support import (  # noqa: E402
    LEGACY_MASTERY_TABLE,
    backfill_mastery,
    mastery_plan,
    table_names,
    upgrade_mastery,
)
from persistence.migration_cli import upgrade_database  # noqa: E402


def database_url(args: argparse.Namespace) -> str:
    if args.database_url:
        return normalize_database_url(args.database_url)
    if args.data_root:
        return f"sqlite+pysqlite:///{Path(args.data_root).expanduser().resolve() / 'dotty.sqlite3'}"
    return normalize_database_url(os.getenv("DATABASE_URL") or build_postgres_url_from_env())


def columns(connection: Any, table_name: str) -> set[str]:
    from persistence.migration_support import column_names

    return column_names(connection, table_name)


def migration_plan(connection: Any) -> dict[str, Any]:
    return mastery_plan(connection)


def apply_schema(connection: Any) -> bool:
    return bool(upgrade_mastery(connection)["masteryRebuilt"])


def backfill(connection: Any, *, apply: bool) -> dict[str, int]:
    return backfill_mastery(connection, apply=apply)


def verify(connection: Any) -> dict[str, Any]:
    from sqlalchemy import text

    plan = mastery_plan(connection)
    tables = table_names(connection)
    null_attempts = connection.scalar(
        text("SELECT COUNT(*) FROM exercise_attempts WHERE knowledge_point_id IS NULL")
    ) if "exercise_attempts" in tables and "knowledge_point_id" in columns(connection, "exercise_attempts") else 0
    null_mastery = connection.scalar(
        text("SELECT COUNT(*) FROM mastery_states WHERE knowledge_point_id IS NULL")
    ) if "mastery_states" in tables and "knowledge_point_id" in columns(connection, "mastery_states") else 0
    return {
        "ready": (
            not plan["missingTables"]
            and not plan["missingColumns"]
            and not plan["missingIndexes"]
            and not plan["masteryRebuildRequired"]
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
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--data-root", default=os.getenv("DOTTY_DATA_DIR"))
    args = parser.parse_args()
    from sqlalchemy import create_engine

    engine = create_engine(database_url(args), future=True)
    if args.dry_run:
        with engine.connect() as connection:
            result = {"mode": "dry-run", "plan": migration_plan(connection), "backfill": backfill(connection, apply=False)}
    elif args.apply:
        formal = upgrade_database(database_url(args))
        with engine.connect() as connection:
            result = {"mode": "apply", **formal, "verify": verify(connection)}
    else:
        with engine.connect() as connection:
            result = {"mode": "verify", **verify(connection)}
    engine.dispose()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ready", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
