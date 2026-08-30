"""Add and backfill immutable variation attribution provenance.

Old variations cannot be reconstructed safely, so the migration deliberately
uses ``unknown`` for every pre-existing row. It is safe to run repeatedly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from importlib import import_module
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPOSITORY_ROOT / "apps" / "api"

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

database_module = import_module("persistence.database")
build_postgres_url_from_env = database_module.build_postgres_url_from_env
normalize_database_url = database_module.normalize_database_url


ALLOWED = {"ai", "self", "unknown"}


def _connect(database_url: str) -> Any:
    return create_engine(database_url, future=True)


def _has_column(engine: Any) -> bool:
    return "attribution_source" in {
        item["name"] for item in inspect(engine).get_columns("variation_exercises")
    }


def migrate(database_url: str, *, apply: bool = False) -> dict[str, Any]:
    engine = _connect(database_url)
    tables = set(inspect(engine).get_table_names())
    if "variation_exercises" not in tables:
        engine.dispose()
        return {"table": False, "column": False, "updated": 0, "invalid": 0}
    column = _has_column(engine)
    updated = 0
    if apply and not column:
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE variation_exercises ADD COLUMN attribution_source VARCHAR(16) NOT NULL DEFAULT 'unknown'"
            ))
        column = True
    if column:
        with engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT variation_id, attribution_source FROM variation_exercises"
            )).all()
        invalid_ids = [row[0] for row in rows if row[1] not in ALLOWED]
        if apply and invalid_ids:
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE variation_exercises SET attribution_source = 'unknown' WHERE attribution_source IS NULL OR attribution_source NOT IN ('ai', 'self', 'unknown')")
                )
            updated = len(invalid_ids)
        invalid = len(invalid_ids)
    else:
        invalid = 0
    engine.dispose()
    return {"table": True, "column": column, "updated": updated, "invalid": invalid}


def verify(database_url: str) -> dict[str, Any]:
    result = migrate(database_url)
    if not result["column"]:
        raise RuntimeError("variation_exercises.attribution_source 不存在")
    engine = _connect(database_url)
    with engine.connect() as connection:
        invalid = connection.execute(text(
            "SELECT COUNT(*) FROM variation_exercises WHERE attribution_source NOT IN ('ai', 'self', 'unknown') OR attribution_source IS NULL"
        )).scalar_one()
    engine.dispose()
    if invalid:
        raise RuntimeError(f"发现 {invalid} 条非法 attribution_source")
    return {**result, "verified": True, "invalid": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--data-root", default=os.getenv("DOTTY_DATA_DIR"))
    args = parser.parse_args()
    if args.database_url:
        database_url = normalize_database_url(args.database_url)
    elif args.data_root:
        database_url = f"sqlite+pysqlite:///{Path(args.data_root).expanduser().resolve() / 'dotty.sqlite3'}"
    else:
        database_url = normalize_database_url(build_postgres_url_from_env())
    if args.verify:
        result = {"mode": "verify", **verify(database_url)}
    else:
        result = {"mode": "dry-run" if args.dry_run else "apply", **migrate(database_url, apply=args.apply)}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("verified", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
