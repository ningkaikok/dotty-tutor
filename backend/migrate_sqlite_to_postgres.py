"""Idempotently migrate the legacy Dotty SQLite metadata into PostgreSQL.

The original PDF, Markdown and image assets remain in ``data/uploads``. Only
database rows are copied; their stored directory paths continue to point at
the same local files.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from storage import DEFAULT_POSTGRES_URL, TutorStore, normalize_database_url


def decoded(value: str | None) -> Any:
    return json.loads(value) if value else None


def migrate(source_path: Path, database_url: str) -> tuple[dict[str, int], dict[str, int]]:
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite 数据库不存在：{source_path}")
    if normalize_database_url(database_url).startswith("sqlite"):
        raise ValueError("目标 DATABASE_URL 必须是 PostgreSQL")

    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    try:
        jobs = source.execute("SELECT * FROM upload_jobs ORDER BY started_at").fetchall()
        questions = source.execute(
            "SELECT * FROM batch_questions ORDER BY created_at, batch_id"
        ).fetchall()
    finally:
        source.close()

    target = TutorStore(database_url=database_url, data_root=source_path.parent)
    try:
        for row in jobs:
            target.save_job({
                "uploadId": row["upload_id"],
                "importId": row["import_id"],
                "filename": row["filename"],
                "contentType": row["content_type"],
                "size": row["size"],
                "chunkSize": row["chunk_size"],
                "totalChunks": row["total_chunks"],
                "sourceText": row["source_text"],
                "directory": Path(row["directory"]),
                "status": row["status"],
                "progress": row["progress"],
                "message": row["message"],
                "result": decoded(row["result_json"]),
                "startedAt": row["started_at"],
                "updatedAt": row["updated_at"],
                "completedAt": row["completed_at"],
            })

        grouped: dict[str, list[tuple[str, dict[str, Any], list[dict[str, Any]]]]] = defaultdict(list)
        for row in questions:
            grouped[row["upload_id"]].append((
                row["batch_id"],
                decoded(row["payload_json"]),
                decoded(row["guide_cards_json"]) or [],
            ))
        for upload_id, records in grouped.items():
            target.save_questions(upload_id, records)

        source_counts = {
            "upload_jobs": len(jobs),
            "batch_questions": len(questions),
        }
        target_counts = target.counts()
        if any(target_counts[name] < count for name, count in source_counts.items()):
            raise RuntimeError(
                f"迁移后行数不足：SQLite={source_counts}，PostgreSQL={target_counts}"
            )
        return source_counts, target_counts
    finally:
        target.close()


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="迁移 Dotty SQLite 数据到 PostgreSQL")
    parser.add_argument(
        "--source",
        type=Path,
        default=project_root / "data" / "dotty.sqlite3",
        help="旧 SQLite 数据库路径",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", DEFAULT_POSTGRES_URL),
        help="目标 PostgreSQL SQLAlchemy URL",
    )
    args = parser.parse_args()
    source_counts, target_counts = migrate(args.source.resolve(), args.database_url)
    print(f"迁移完成：SQLite {source_counts} -> PostgreSQL {target_counts}")


if __name__ == "__main__":
    main()
