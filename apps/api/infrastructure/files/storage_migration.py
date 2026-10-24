"""Inspect or explicitly backfill existing local files to private Storage.

Run from ``apps/api`` as ``uv run python -m infrastructure.files.storage_migration inspect``
or ``... migrate``. Inspect is read-only; migrate is repeatable and never removes
the local originals.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy import select

from infrastructure.files.file_storage import (
    FileStorageConfigurationError,
    FileStorageError,
)
from persistence.app_store import AppStore
from persistence.mistake_store import MistakeStore, mistake_items
from persistence.schema import upload_jobs


def _regular_files(directory: Path) -> tuple[list[Path], bool]:
    if not directory.is_dir() or directory.is_symlink():
        return [], False
    files: list[Path] = []
    has_symlink = False
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            has_symlink = True
        elif path.is_file() and not path.name.endswith(".partial"):
            files.append(path)
    return files, has_symlink


def _upload_records(store: AppStore) -> list[tuple[str, Path]]:
    with store.engine.connect() as connection:
        ids = connection.execute(select(upload_jobs.c.upload_id).where(
            upload_jobs.c.status != "deleted",
        ).order_by(upload_jobs.c.upload_id)).scalars().all()
    records: list[tuple[str, Path]] = []
    root = store.upload_root.resolve()
    for upload_id in ids:
        job = store.load_job(str(upload_id))
        if not job:
            continue
        directory = Path(job["directory"]).resolve(strict=False)
        # Older utility rows occasionally recorded data_root itself. Never let
        # a bulk command interpret that as authority to upload unrelated data.
        if directory.parent == root and directory.name == str(upload_id):
            records.append((str(upload_id), directory))
    return records


def _mistake_records(store: AppStore, mistakes: MistakeStore) -> list[tuple[str, str, Path]]:
    with store.engine.connect() as connection:
        rows = connection.execute(select(mistake_items).order_by(mistake_items.c.mistake_id)).mappings().all()
    records: list[tuple[str, str, Path]] = []
    root = mistakes.mistake_root.resolve()
    for row in rows:
        item = mistakes._serialize(row)
        source_path = Path(str(item.get("sourceImagePath") or ""))
        if not source_path.is_absolute():
            continue
        try:
            relative = source_path.resolve(strict=False).relative_to(root)
        except ValueError:
            continue
        if relative.parts and relative.parts[0] == str(row["mistake_id"]):
            records.append((str(row["learner_id"]), str(row["mistake_id"]), source_path.parent))
    return records


def _inventory(store: AppStore, mistakes: MistakeStore) -> dict[str, Any]:
    upload_records = _upload_records(store)
    mistake_records = _mistake_records(store, mistakes)
    counts = {"uploads": 0, "mistakes": 0, "files": 0, "bytes": 0, "missingDirectories": 0, "symlinkDirectories": 0}
    for _upload_id, directory in upload_records:
        files, has_symlink = _regular_files(directory)
        counts["uploads"] += 1
        counts["missingDirectories"] += int(not directory.is_dir())
        counts["files"] += len(files)
        counts["bytes"] += sum(path.stat().st_size for path in files)
        counts["symlinkDirectories"] += int(has_symlink)
    for _learner_id, _mistake_id, directory in mistake_records:
        files, has_symlink = _regular_files(directory)
        counts["mistakes"] += 1
        counts["missingDirectories"] += int(not directory.is_dir())
        counts["files"] += len(files)
        counts["bytes"] += sum(path.stat().st_size for path in files)
        counts["symlinkDirectories"] += int(has_symlink)
    return {"records": counts}


def _migrate(store: AppStore, mistakes: MistakeStore) -> dict[str, Any]:
    if not store.file_assets.enabled:
        raise FileStorageConfigurationError("显式迁移本机文件前必须启用 Supabase 文件存储")
    totals = {"uploads": 0, "mistakes": 0, "files": 0, "bytes": 0, "failures": 0}
    failures: list[dict[str, str]] = []
    for upload_id, directory in _upload_records(store):
        try:
            result = store.file_assets.migrate_upload_directory(upload_id, directory)
            totals["uploads"] += 1
            totals["files"] += result["files"]
            totals["bytes"] += result["bytes"]
        except Exception as error:
            totals["failures"] += 1
            failures.append({"kind": "upload", "recordId": upload_id, "errorType": type(error).__name__})
    for learner_id, mistake_id, directory in _mistake_records(store, mistakes):
        try:
            files, _has_symlink = _regular_files(directory)
            byte_count = sum(path.stat().st_size for path in files)
            count = mistakes.persist_capture_files(learner_id, mistake_id, directory)
            totals["mistakes"] += 1
            totals["files"] += count
            totals["bytes"] += byte_count
        except Exception as error:
            totals["failures"] += 1
            failures.append({"kind": "mistake", "recordId": mistake_id, "errorType": type(error).__name__})
    return {"migration": totals, "failures": failures, "originalsRemoved": False}


def run(command: str, *, database_url: str | None = None) -> tuple[int, dict[str, Any]]:
    store = AppStore(database_url=database_url)
    file_assets = store.file_assets if command == "migrate" else None
    mistakes = MistakeStore(engine=store.engine, data_root=store.root, file_assets=file_assets)
    try:
        if command == "inspect":
            report = _inventory(store, mistakes)
            report["storageBackend"] = os.getenv("DOTTY_FILE_STORAGE_BACKEND", "local").strip().lower()
            return 0, report
        result = _migrate(store, mistakes)
        return (1 if result["migration"]["failures"] else 0), result
    finally:
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "migrate"), help="inspect is read-only; migrate copies files remotely")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()
    try:
        code, result = run(args.command, database_url=args.database_url)
    except Exception as error:
        message = str(error) if isinstance(error, (FileStorageError, ValueError)) else "命令执行失败，请检查数据库与远端配置"
        result = {"errorType": type(error).__name__, "message": message}
        code = 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "run"]
