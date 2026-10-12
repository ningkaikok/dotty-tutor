#!/usr/bin/env python3
"""Safely plan or reverse a reviewed legacy textbook-output cleanup.

Running without a subcommand only creates a read-only dry-run manifest. Applying
or restoring requires explicit manifest and environment fingerprints.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# This standalone script lives outside apps/api but imports its service modules.
API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from sqlalchemy import create_engine  # noqa: E402

from application.services.legacy_textbook_cleanup import (  # noqa: E402
    LegacyTextbookCleanupError,
    LegacyTextbookCleanupService,
)
from persistence.database import resolve_database_url  # noqa: E402
from persistence.legacy_textbook_cleanup_store import (  # noqa: E402
    LegacyTextbookCleanupStore,
)


def _read_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise LegacyTextbookCleanupError("manifest 必须是普通文件")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LegacyTextbookCleanupError("manifest 无法读取") from error
    if not isinstance(value, dict):
        raise LegacyTextbookCleanupError("manifest 顶层格式错误")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("dry-run", "apply", "restore"), default="dry-run")
    parser.add_argument("--upload-id", help="唯一目标 upload ID，仅 dry-run 使用")
    parser.add_argument("--manifest", type=Path, help="dry-run 输出或 apply 输入 manifest 文件")
    parser.add_argument("--backup", type=Path, help="apply 写入或 restore 读取的恢复快照")
    parser.add_argument("--manifest-sha256", help="人工核对后的 manifest SHA-256")
    parser.add_argument("--target-environment-fingerprint", help="人工核对后的目标环境指纹")
    parser.add_argument("--database-url", help="PostgreSQL URL；未提供时使用项目环境配置")
    parser.add_argument("--now", type=float, help="可复现验收用时间戳")
    return parser


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "dry-run" and not args.upload_id:
        _parser().error("dry-run 必须提供 --upload-id")
    if args.command == "apply":
        if not args.manifest or not args.backup:
            _parser().error("apply 必须提供 --manifest 和 --backup")
        if not args.manifest_sha256 or not args.target_environment_fingerprint:
            _parser().error("apply 必须显式确认 --manifest-sha256 与 --target-environment-fingerprint")
    if args.command == "restore":
        if not args.backup or not args.manifest_sha256 or not args.target_environment_fingerprint:
            _parser().error("restore 必须提供 --backup、--manifest-sha256 与 --target-environment-fingerprint")

    engine = create_engine(resolve_database_url(args.database_url), future=True)
    try:
        service = LegacyTextbookCleanupService(LegacyTextbookCleanupStore(engine))
        if args.command == "dry-run":
            planned = service.plan([args.upload_id], now=args.now)
            if args.manifest:
                service.write_manifest(planned["manifest"], args.manifest)
                planned["manifestPath"] = str(args.manifest.expanduser().resolve())
            _emit(planned)
            return 0
        if args.command == "apply":
            manifest = _read_object(args.manifest)
            result = service.apply(
                manifest,
                expected_manifest_sha256=args.manifest_sha256,
                expected_target_environment_fingerprint=args.target_environment_fingerprint,
                backup_path=args.backup,
                now=args.now,
            )
            _emit(result)
            return 0
        result = service.restore(
            args.backup,
            expected_manifest_sha256=args.manifest_sha256,
            expected_target_environment_fingerprint=args.target_environment_fingerprint,
            now=args.now,
        )
        _emit(result)
        return 0
    except (LegacyTextbookCleanupError, LookupError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
