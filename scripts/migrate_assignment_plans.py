"""Create assignment planning drafts and the plan-backed assignment link.

Run ``--dry-run`` before ``--apply`` and ``--verify`` after applying to an
existing database.  The migration never rewrites historical assignments; their
``assignment_plan_id`` remains NULL by design.
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

from persistence.database import build_postgres_url_from_env, normalize_database_url  # noqa: E402
from persistence.schema import metadata  # noqa: E402


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
    missing_columns = {}
    if "assignments" in tables and "assignment_plan_id" not in columns(connection, "assignments"):
        missing_columns["assignments"] = ["assignment_plan_id"]
    assignment_indexes = {item["name"] for item in inspect(connection).get_indexes("assignments")} if "assignments" in tables else set()
    plan_indexes = {item["name"] for item in inspect(connection).get_indexes("assignment_plans")} if "assignment_plans" in tables else set()
    return {
        "missingTables": sorted({"assignment_plans"} - tables),
        "missingColumns": missing_columns,
        "missingIndexes": sorted(
            (set() if "idx_assignments_plan" in assignment_indexes else {"idx_assignments_plan"})
            | (set() if "idx_assignment_plans_class" in plan_indexes else {"idx_assignment_plans_class"})
        ),
    }


def apply_schema(connection: Any) -> None:
    metadata.create_all(connection)
    if "assignment_plan_id" not in columns(connection, "assignments"):
        connection.execute(text("ALTER TABLE assignments ADD COLUMN assignment_plan_id VARCHAR(64)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS idx_assignments_plan ON assignments (assignment_plan_id, created_at DESC)"))
    connection.execute(text("CREATE INDEX IF NOT EXISTS idx_assignment_plans_class ON assignment_plans (class_id, created_at DESC)"))


def verify(connection: Any) -> dict[str, Any]:
    plan = migration_plan(connection)
    return {"ready": not plan["missingTables"] and not plan["missingColumns"] and not plan["missingIndexes"], "plan": plan}


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
