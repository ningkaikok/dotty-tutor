"""统一数据库迁移命令：current、head、preflight、upgrade、verify。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from persistence.database import resolve_database_url as resolve_configured_database_url
from persistence.migration_support import schema_report

API_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_CONFIG = API_ROOT / "alembic.ini"


def resolve_database_url(database_url: str | None) -> str:
    return resolve_configured_database_url(database_url)


def alembic_config(database_url: str | None = None) -> Config:
    config = Config(str(ALEMBIC_CONFIG))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    if database_url is not None:
        config.attributes["database_url"] = database_url
    return config


def head_revision(config: Config) -> str:
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise RuntimeError("Alembic 未找到 head revision")
    return head


def current_revision(database_url: str) -> str | None:
    engine = create_engine(database_url, future=True)
    try:
        with engine.connect() as connection:
            if "alembic_version" not in inspect(connection).get_table_names():
                return None
            value = connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version LIMIT 1"
            ).scalar()
            return str(value) if value is not None else None
    finally:
        engine.dispose()


def upgrade_database(database_url: str) -> dict[str, Any]:
    """Run the formal Alembic upgrade and return sanitized version metadata."""
    config = alembic_config(database_url)
    head = head_revision(config)
    command.upgrade(config, "head")
    return {"current": current_revision(database_url), "head": head, "upgraded": True}


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if args.command == "head":
        config = alembic_config()
        return 0, {"head": head_revision(config)}
    database_url = resolve_database_url(args.database_url)
    config = alembic_config(database_url)
    head = head_revision(config)
    if args.command == "current":
        return 0, {"current": current_revision(database_url), "head": head}
    if args.command == "upgrade":
        return 0, upgrade_database(database_url)
    engine = create_engine(database_url, future=True)
    try:
        report = schema_report(engine, require_version=args.command == "verify")
    finally:
        engine.dispose()
    return (0 if report["ready"] else 1), report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("current", "head", "preflight", "upgrade", "verify"))
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()
    code, result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
