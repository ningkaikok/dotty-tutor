"""Shared source-row lock protocol for reversible legacy-content cleanup.

Writers of old derived records take a shared lock on the owning upload row and
inspect the exact applied marker. Cleanup takes an exclusive lock on that same
row before inspecting and tombstoning, so either it sees the committed write or
the writer sees the committed marker.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Connection

from persistence.database import decode_json
from persistence.legacy_textbook_cleanup_store import legacy_textbook_cleanup_manifests
from persistence.schema import chapter_records, upload_jobs


def lock_upload_for_legacy_write(connection: Connection, upload_id: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Lock a source upload for a derived write and return its active tombstone."""
    row = connection.execute(select(
        upload_jobs.c.status,
    ).where(upload_jobs.c.upload_id == upload_id).with_for_update(read=True)).mappings().first()
    if not row:
        # Some migration and unit fixtures intentionally use an unpersisted
        # source identifier; there is no cleanup marker/lock owner to race.
        return {}, None
    if row["status"] in {"deleted", "deleting"}:
        raise ValueError("教材来源不可用于生成派生产物")
    marker_row = connection.execute(select(
        legacy_textbook_cleanup_manifests.c.snapshot_json,
    ).where(
        legacy_textbook_cleanup_manifests.c.upload_id == upload_id,
        legacy_textbook_cleanup_manifests.c.status == "applied",
    )).mappings().first()
    marker = decode_json(marker_row["snapshot_json"]) if marker_row else None
    return dict(row), marker if isinstance(marker, dict) else None


def tombstoned_ids(marker: dict[str, Any] | None, key: str) -> set[str]:
    """Read only the exact recorded identifiers used by the recovery manifest."""
    if not marker:
        return set()
    values = marker.get(key, [])
    return {str(value) for value in values} if isinstance(values, list) else set()


def legacy_job_is_tombstoned(connection: Connection, upload_id: str, job_id: str) -> bool:
    """Hide exact legacy jobs without suppressing later source-preparation work."""
    marker_row = connection.execute(select(
        legacy_textbook_cleanup_manifests.c.snapshot_json,
    ).where(
        legacy_textbook_cleanup_manifests.c.upload_id == upload_id,
        legacy_textbook_cleanup_manifests.c.status == "applied",
    )).mappings().first()
    marker = decode_json(marker_row["snapshot_json"]) if marker_row else None
    return job_id in tombstoned_ids(marker if isinstance(marker, dict) else None, "jobIds")


def active_cleanup_snapshots(connection: Connection) -> list[dict[str, Any]]:
    """Load active exact-ID manifests for readers whose old rows lack upload IDs."""
    rows = connection.execute(select(
        legacy_textbook_cleanup_manifests.c.snapshot_json,
    ).where(legacy_textbook_cleanup_manifests.c.status == "applied")).mappings().all()
    snapshots = [decode_json(row["snapshot_json"]) for row in rows]
    return [item for item in snapshots if isinstance(item, dict)]


def job_is_tombstoned(connection: Connection, job_id: str) -> bool:
    """Filter a job by its exact ID even when its payload predates uploadId."""
    return any(job_id in tombstoned_ids(snapshot, "jobIds") for snapshot in active_cleanup_snapshots(connection))


def exact_id_is_tombstoned(connection: Connection, identifier: str, key: str) -> bool:
    """Check an exact legacy identifier across active manifests."""
    return any(identifier in tombstoned_ids(snapshot, key) for snapshot in active_cleanup_snapshots(connection))


def payload_targets_any_tombstoned_output(connection: Connection, payload: dict[str, Any]) -> bool:
    """Match legacy object references against every active exact-ID snapshot."""
    return any(payload_targets_tombstoned_output(payload, marker) for marker in active_cleanup_snapshots(connection))


def source_upload_id_for_payload(connection: Connection, payload: dict[str, Any]) -> str | None:
    """Resolve a legacy chapter job's upload so writes share cleanup's row lock."""
    chapter_ids: set[str] = set()
    id_keys = {"chapterId", "chapter_id", "chapterIds", "chapter_ids"}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in id_keys:
                    if isinstance(child, str):
                        chapter_ids.add(child)
                    elif isinstance(child, list):
                        chapter_ids.update(str(item) for item in child if isinstance(item, str))
                if isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, (dict, list)):
                    visit(child)

    visit(payload)
    if not chapter_ids:
        return None
    rows = connection.execute(select(chapter_records.c.chapter_json).where(
        chapter_records.c.chapter_id.in_(chapter_ids),
    )).mappings().all()
    for row in rows:
        chapter = decode_json(row["chapter_json"])
        revisions = chapter.get("sourceRevisions", []) if isinstance(chapter, dict) else []
        if isinstance(revisions, list):
            for revision in reversed(revisions):
                if isinstance(revision, dict) and revision.get("uploadId"):
                    return str(revision["uploadId"])
    return None


def payload_targets_tombstoned_output(payload: dict[str, Any], marker: dict[str, Any] | None) -> bool:
    """Reject a new old-pipeline command that points at a retired exact record."""
    fields = {
        "chapterId": "chapterIds", "chapter_id": "chapterIds",
        "chapterIds": "chapterIds", "chapter_ids": "chapterIds",
        "lessonId": "lessonIds", "lesson_id": "lessonIds",
        "lessonIds": "lessonIds", "lesson_ids": "lessonIds",
        "batchId": "batchIds", "batch_id": "batchIds",
        "batchIds": "batchIds", "batch_ids": "batchIds",
        "questionId": "questionIds", "question_id": "questionIds",
        "questionIds": "questionIds", "question_ids": "questionIds",
    }

    def visit(value: Any) -> bool:
        if isinstance(value, dict):
            for key, child in value.items():
                target = fields.get(str(key))
                if target:
                    retired = tombstoned_ids(marker, target)
                    if isinstance(child, list):
                        if any(str(item) in retired for item in child):
                            return True
                    elif str(child) in retired:
                        return True
                if isinstance(child, (dict, list)) and visit(child):
                    return True
        elif isinstance(value, list):
            return any(visit(child) for child in value if isinstance(child, (dict, list)))
        return False

    return visit(payload)


def active_cleanup_snapshot(connection: Connection, upload_id: str) -> dict[str, Any] | None:
    """Read the active exact-ID filter for normal source projections."""
    row = connection.execute(select(
        legacy_textbook_cleanup_manifests.c.snapshot_json,
    ).where(
        legacy_textbook_cleanup_manifests.c.upload_id == upload_id,
        legacy_textbook_cleanup_manifests.c.status == "applied",
    )).mappings().first()
    value = decode_json(row["snapshot_json"]) if row else None
    return value if isinstance(value, dict) else None
