"""Deprecated compatibility wrapper for the Alembic assignment migration.

Use ``python -m persistence.migration_cli`` with ``preflight``, ``upgrade`` or
``verify`` from ``apps/api``. This wrapper remains for operators who used the
v0.27.0 command.
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

from persistence.database import resolve_database_url  # noqa: E402
from persistence.migration_cli import upgrade_database  # noqa: E402
from persistence.migration_support import assignment_plan, table_names, upgrade_assignments  # noqa: E402


def database_url(args: argparse.Namespace) -> str:
    return resolve_database_url(args.database_url)


def migration_plan(connection: Any) -> dict[str, Any]:
    plan = assignment_plan(connection)
    tables = table_names(connection)
    return {
        "missingTables": sorted({"learning_classes", "class_memberships", "assignments"} - tables),
        "missingColumns": plan["missingColumns"],
    }


def apply_schema(connection: Any) -> None:
    upgrade_assignments(connection)


def verify(connection: Any) -> dict[str, Any]:
    plan = migration_plan(connection)
    return {"ready": not plan["missingTables"] and not plan["missingColumns"], "plan": plan}


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
            result = {"mode": "dry-run", "plan": migration_plan(connection)}
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
