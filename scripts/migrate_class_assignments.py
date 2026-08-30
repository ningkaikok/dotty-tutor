"""Create class/assignment tables and add assignment identity to learning sessions.

The application still uses ``create_all`` for fresh local databases, but an
existing database needs this explicit, repeatable migration because SQLAlchemy
does not alter an already-created table. Run ``--dry-run`` before ``--apply``
and use ``--verify`` after the change.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPOSITORY_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from persistence.database import build_postgres_url_from_env, normalize_database_url
from persistence.schema import metadata


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


def migration_plan(connection: Any) -> dict[str, Any]:
    tables = table_names(connection)
    expected_tables = {"learning_classes", "class_memberships", "assignments"}
    missing_columns = {}
    if "learning_sessions" in tables and "assignment_id" not in columns(connection, "learning_sessions"):
        missing_columns["learning_sessions"] = ["assignment_id"]
    return {
        "missingTables": sorted(expected_tables - tables),
        "missingColumns": missing_columns,
    }


def apply_schema(connection: Any) -> None:
    metadata.create_all(connection)
    if "assignment_id" not in columns(connection, "learning_sessions"):
        connection.execute(text(
            "ALTER TABLE learning_sessions ADD COLUMN assignment_id VARCHAR(64)"
        ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_learning_sessions_assignment "
        "ON learning_sessions (assignment_id, learner_id, updated_at)"
    ))


def verify(connection: Any) -> dict[str, Any]:
    plan = migration_plan(connection)
    return {"ready": not plan["missingTables"] and not plan["missingColumns"], "plan": plan}


def main() -> int:
    args = parse_args()
    engine = create_engine(database_url(args), future=True)
    if args.dry_run:
        with engine.connect() as connection:
            result = {"mode": "dry-run", "plan": migration_plan(connection)}
    elif args.apply:
        with engine.begin() as connection:
            apply_schema(connection)
            result = {"mode": "apply", **verify(connection)}
    else:
        with engine.connect() as connection:
            result = {"mode": "verify", **verify(connection)}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ready", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
