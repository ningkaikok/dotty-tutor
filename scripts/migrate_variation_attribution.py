"""Deprecated compatibility wrapper for the Alembic variation migration."""

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

from persistence.database import resolve_database_url  # noqa: E402
from persistence.migration_cli import upgrade_database  # noqa: E402
from persistence.migration_support import add_missing_columns  # noqa: E402

ALLOWED = {"ai", "self", "unknown"}


def _has_column(engine: Any) -> bool:
    if "variation_exercises" not in inspect(engine).get_table_names():
        return False
    return "attribution_source" in {
        item["name"] for item in inspect(engine).get_columns("variation_exercises")
    }


def migrate(database_url: str, *, apply: bool = False) -> dict[str, Any]:
    engine = create_engine(database_url, future=True)
    tables = set(inspect(engine).get_table_names())
    if "variation_exercises" not in tables:
        engine.dispose()
        return {"table": False, "column": False, "updated": 0, "invalid": 0}
    column = _has_column(engine)
    updated = 0
    if apply and not column:
        with engine.begin() as connection:
            add_missing_columns(connection)
        column = True
    invalid = 0
    if column:
        with engine.connect() as connection:
            invalid_ids = [
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT variation_id FROM variation_exercises "
                        "WHERE attribution_source IS NULL OR attribution_source "
                        "NOT IN ('ai', 'self', 'unknown')"
                    )
                ).all()
            ]
        invalid = len(invalid_ids)
        if apply and invalid_ids:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE variation_exercises SET attribution_source = 'unknown' "
                        "WHERE attribution_source IS NULL OR attribution_source "
                        "NOT IN ('ai', 'self', 'unknown')"
                    )
                )
            updated = invalid
    engine.dispose()
    return {"table": True, "column": column, "updated": updated, "invalid": invalid}


def verify(database_url: str) -> dict[str, Any]:
    result = migrate(database_url)
    if not result["column"]:
        raise RuntimeError("variation_exercises.attribution_source 不存在")
    if result["invalid"]:
        raise RuntimeError(f"发现 {result['invalid']} 条非法 attribution_source")
    return {**result, "verified": True, "invalid": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--data-root", default=os.getenv("DOTTY_DATA_DIR"))
    args = parser.parse_args()
    database_url = resolve_database_url(args.database_url)
    if args.verify:
        result = {"mode": "verify", **verify(database_url)}
    elif args.apply:
        formal = upgrade_database(database_url)
        result = {"mode": "apply", **formal, "verify": verify(database_url)}
    else:
        result = {"mode": "dry-run", **migrate(database_url)}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("verified", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
