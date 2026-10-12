"""Plan and safely execute reversible withdrawal of obsolete legacy outputs."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from persistence.legacy_textbook_cleanup_store import (
    LegacyTextbookCleanupStore,
    canonical_json,
)


class LegacyTextbookCleanupError(ValueError):
    """Raised when a manifest, target, or recovery snapshot is unsafe to use."""


def manifest_sha256(manifest: dict[str, Any]) -> str:
    """Return the canonical digest a human must confirm before apply or restore."""
    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _write_private_json(path: Path, value: dict[str, Any]) -> None:
    """Create a 0600 metadata file atomically and refuse different-file overwrite."""
    encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    path = path.expanduser()
    if path.is_symlink():
        raise LegacyTextbookCleanupError("拒绝通过符号链接写 manifest/backup")
    path = path.absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            existing = _read_json_object(path)
            if existing != value:
                raise LegacyTextbookCleanupError("目标文件已存在且内容不同，拒绝覆盖")
        os.chmod(path, 0o600)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        raise
    finally:
        temporary_path.unlink(missing_ok=True)


def _read_json_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise LegacyTextbookCleanupError("manifest/backup 必须是普通文件")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LegacyTextbookCleanupError("manifest/backup 无法读取") from error
    if not isinstance(value, dict):
        raise LegacyTextbookCleanupError("manifest/backup 顶层格式错误")
    return value


def target_environment(engine: Engine, data_root: Path) -> dict[str, Any]:
    """Identify a database and source directory without exposing connection secrets."""
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT current_database() AS database_name, current_schema() AS schema_name, "
            "inet_server_addr()::text AS server_address, inet_server_port() AS server_port, "
            "current_setting('server_version') AS server_version"
        )).mappings().one()
        tables = set(inspect(connection).get_table_names())
        schema_revision = None
        if "alembic_version" in tables:
            schema_revision = connection.execute(text(
                "SELECT version_num FROM alembic_version LIMIT 1"
            )).scalar_one_or_none()
    url = engine.url
    target = {
        "database": str(row["database_name"]),
        "schema": str(row["schema_name"] or "public"),
        "host": str(row["server_address"] or url.host or "local-socket"),
        "port": int(row["server_port"] or url.port or 0),
        "serverVersion": str(row["server_version"]),
        "schemaRevision": str(schema_revision) if schema_revision else None,
        "dataRoot": str(data_root.expanduser().resolve()),
    }
    target["fingerprint"] = _digest(target)
    return target


def _blocked_reasons(target: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    upload = target["upload"]
    if upload["materialKind"] != "textbook":
        reasons.append("material_kind_not_confirmed_textbook")
    if upload["status"] not in {"completed", "complete", "ready", "succeeded"}:
        reasons.append("source_upload_not_complete")
    if not target["chapters"]:
        reasons.append("no_exact_source_revision_matches")
    if any(chapter["deletedAt"] is not None for chapter in target["chapters"]):
        reasons.append("chapter_already_tombstoned")
    if any(chapter["sourceSha256"] is None for chapter in target["chapters"]):
        reasons.append("source_revision_hash_missing_or_conflicting")
    asset_hash = target["assetFingerprint"].get("sourcePdfSha256")
    revision_hashes = {chapter["sourceSha256"] for chapter in target["chapters"] if chapter.get("sourceSha256")}
    if not target["assetFingerprint"].get("sourcePdfExists"):
        reasons.append("source_pdf_missing")
    if asset_hash and revision_hashes and revision_hashes != {asset_hash}:
        reasons.append("source_pdf_hash_mismatch")
    blocked_refs = {
        key: count for key, count in target["protectedRefs"].items()
        if count and key not in {"questionRevisions"}
    }
    if blocked_refs:
        reasons.append("protected_references_present")
    if any(lesson.get("reviewed") or lesson.get("teacherEditCount") for lesson in target["lessons"]):
        reasons.append("lesson_has_teacher_review_or_edit")
    if any(chapter.get("reviewMetadataCount") for chapter in target["chapters"]):
        reasons.append("chapter_has_teacher_review")
    return sorted(set(reasons))


def _validate_manifest(manifest: dict[str, Any]) -> None:
    expected_keys = {
        "schemaVersion", "manifestId", "createdAt", "targetEnvironment",
        "targetEnvironmentFingerprint", "targets",
    }
    if set(manifest) != expected_keys or manifest.get("schemaVersion") != 1:
        raise LegacyTextbookCleanupError("manifest schema 无效")
    if not isinstance(manifest.get("manifestId"), str) or not manifest["manifestId"]:
        raise LegacyTextbookCleanupError("manifest 缺少 ID")
    if not isinstance(manifest.get("targets"), list) or len(manifest["targets"]) != 1:
        raise LegacyTextbookCleanupError("每个 manifest 必须绑定唯一 upload target")
    target = manifest["targets"][0]
    if not isinstance(target, dict) or set(target) != {
        "upload", "fingerprint", "chapters", "lessons", "batchQuestions", "publications",
        "jobs", "protectedRefs", "assetFingerprint", "chapterIds", "lessonIds", "batchIds",
        "jobIds", "qualityCaseIds", "mediaIds", "voiceSessionIds", "coursewareRecordIds",
        "qualityReviewPackageIds", "qualityCaseRevisionIds", "qualityDecisionIds", "qualityReviewRunIds",
        "questionIds", "questionRevisionIds", "revisionSourceValues", "runSnapshotIds",
        "lessonSearchDocumentIds", "eligibleToApply", "eligibilityReasons",
    }:
        raise LegacyTextbookCleanupError("manifest target 字段不完整或包含未审核字段")
    if not isinstance(target.get("eligibleToApply"), bool) or not isinstance(target.get("eligibilityReasons"), list):
        raise LegacyTextbookCleanupError("manifest eligibility 字段格式错误")


class LegacyTextbookCleanupService:
    """Build exact dry-run manifests and execute only explicit reviewed snapshots."""

    def __init__(self, store: LegacyTextbookCleanupStore) -> None:
        self.store = store

    def plan(self, upload_ids: list[str], *, now: float | None = None) -> dict[str, Any]:
        """Inspect a single exact upload and return a non-mutating review artifact."""
        normalized = sorted({item.strip() for item in upload_ids if isinstance(item, str) and item.strip()})
        if len(normalized) != 1:
            raise LegacyTextbookCleanupError("一次计划必须指定一个且仅一个 uploadId")
        environment = target_environment(self.store.engine, self.store.data_root)
        inspection = self.store.inspect_upload(normalized[0])
        reasons = _blocked_reasons(inspection)
        if inspection["upload"]["materialKind"] == "paper":
            raise LegacyTextbookCleanupError("识别为试卷的上传不得清理为教材")
        if inspection["upload"]["materialKind"] == "unknown":
            raise LegacyTextbookCleanupError("未知材料类型需要先取得教师明确教材确认凭据")
        target = {
            **inspection,
            "eligibleToApply": not reasons,
            "eligibilityReasons": reasons,
        }
        manifest = {
            "schemaVersion": 1,
            "manifestId": uuid.uuid4().hex,
            "createdAt": float(now if now is not None else time.time()),
            "targetEnvironment": environment,
            "targetEnvironmentFingerprint": environment["fingerprint"],
            "targets": [target],
        }
        _validate_manifest(manifest)
        return {
            "manifest": manifest,
            "manifestSha256": manifest_sha256(manifest),
            "targetEnvironmentFingerprint": environment["fingerprint"],
            "status": "dry_run",
        }

    def write_manifest(self, manifest: dict[str, Any], path: str | Path) -> str:
        """Persist a metadata-only manifest with private permissions."""
        _validate_manifest(manifest)
        _write_private_json(Path(path), manifest)
        return manifest_sha256(manifest)

    def apply(
        self,
        manifest: dict[str, Any],
        *,
        expected_manifest_sha256: str,
        expected_target_environment_fingerprint: str,
        backup_path: str | Path,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Apply a human-confirmed manifest after writing its restorable CAS backup."""
        _validate_manifest(manifest)
        actual_manifest_hash = manifest_sha256(manifest)
        if actual_manifest_hash != expected_manifest_sha256:
            raise LegacyTextbookCleanupError("manifest SHA-256 与确认值不一致")
        environment = target_environment(self.store.engine, self.store.data_root)
        if environment["fingerprint"] != expected_target_environment_fingerprint:
            raise LegacyTextbookCleanupError("目标数据库或素材目录与确认指纹不一致")
        if manifest["targetEnvironmentFingerprint"] != environment["fingerprint"]:
            raise LegacyTextbookCleanupError("manifest 目标环境已变化")
        target = manifest["targets"][0]
        reasons = _blocked_reasons(target)
        if reasons or not target["eligibleToApply"]:
            raise LegacyTextbookCleanupError("manifest 包含受保护引用或不满足清理前置条件")
        backup_file = Path(backup_path).expanduser()
        if backup_file.is_symlink():
            raise LegacyTextbookCleanupError("拒绝通过符号链接写恢复快照")
        backup_file = backup_file.absolute()
        backup: dict[str, Any]
        if backup_file.exists():
            backup = _read_json_object(backup_file)
            self._validate_backup(backup, manifest, actual_manifest_hash, environment["fingerprint"])
            snapshots = backup["snapshots"]
        else:
            current = self.store.inspect_upload(target["upload"]["uploadId"])
            if current["fingerprint"] != target["fingerprint"]:
                raise LegacyTextbookCleanupError("目标材料关联或版本已变化，需重新生成 manifest")
            snapshot = self.store.snapshot_upload(
                target["upload"]["uploadId"],
                expected_fingerprint=target["fingerprint"],
                manifest_id=manifest["manifestId"],
            )
            backup = {
                "schemaVersion": 1,
                "manifestId": manifest["manifestId"],
                "manifestSha256": actual_manifest_hash,
                "targetEnvironmentFingerprint": environment["fingerprint"],
                "createdAt": float(now if now is not None else time.time()),
                "snapshots": [snapshot],
            }
            backup["snapshotSha256"] = _digest(backup["snapshots"])
            self._validate_backup(backup, manifest, actual_manifest_hash, environment["fingerprint"])
            _write_private_json(backup_file, backup)
            snapshots = backup["snapshots"]

        applied = []
        for snapshot in snapshots:
            applied.append(self.store.apply_upload_cleanup(
                snapshot,
                manifest_id=manifest["manifestId"],
                now=now,
            ))
        result = applied[0] if len(applied) == 1 else {}
        return {
            "status": "deleted",
            "manifestId": manifest["manifestId"],
            "targetEnvironmentFingerprint": environment["fingerprint"],
            "backupPath": str(backup_file),
            "backupSha256": _digest(backup),
            "results": applied,
            **{
                key: result[key]
                for key in ("uploadId", "fingerprint", "chapterIds", "lessonIds", "batchIds", "questionIds", "jobIds", "changedChapters", "cancelledJobs")
                if key in result
            },
            "idempotent": all(bool(item.get("idempotent")) for item in applied),
        }

    def restore(
        self,
        backup_path: str | Path,
        *,
        expected_manifest_sha256: str,
        expected_target_environment_fingerprint: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Restore only marker-owned chapter tombstones while leaving job cancellation terminal."""
        backup_file = Path(backup_path).expanduser()
        if backup_file.is_symlink():
            raise LegacyTextbookCleanupError("恢复快照必须是普通文件，不能是符号链接")
        backup = _read_json_object(backup_file)
        if set(backup) != {
            "schemaVersion", "manifestId", "manifestSha256", "targetEnvironmentFingerprint",
            "createdAt", "snapshots", "snapshotSha256",
        } or backup.get("schemaVersion") != 1:
            raise LegacyTextbookCleanupError("backup schema 无效")
        if backup["manifestSha256"] != expected_manifest_sha256:
            raise LegacyTextbookCleanupError("backup manifest SHA-256 与确认值不一致")
        if backup["snapshotSha256"] != _digest(backup["snapshots"]):
            raise LegacyTextbookCleanupError("backup snapshot SHA-256 校验失败")
        environment = target_environment(self.store.engine, self.store.data_root)
        if environment["fingerprint"] != expected_target_environment_fingerprint:
            raise LegacyTextbookCleanupError("目标数据库或素材目录与确认指纹不一致")
        if backup["targetEnvironmentFingerprint"] != environment["fingerprint"]:
            raise LegacyTextbookCleanupError("backup 目标环境与当前环境不一致")
        if not isinstance(backup["snapshots"], list) or len(backup["snapshots"]) != 1:
            raise LegacyTextbookCleanupError("backup 必须含唯一 upload snapshot")
        snapshot = backup["snapshots"][0]
        self._validate_snapshot_shape(snapshot)
        restored = self.store.restore_upload(
            snapshot,
            manifest_id=backup["manifestId"],
            now=now,
        )
        return {
            **restored,
            "targetEnvironmentFingerprint": environment["fingerprint"],
            "backupPath": str(backup_file.absolute()),
        }

    @staticmethod
    def _validate_snapshot_shape(snapshot: dict[str, Any]) -> None:
        allowed = {
            "snapshotId", "manifestId", "uploadId", "fingerprint", "previousUploadStatus",
            "previousUploadUpdatedAt", "chapterIds", "lessonIds", "batchIds", "questionIds",
            "questionRevisionIds", "jobIds", "runSnapshotIds", "lessonSearchDocumentIds",
            "sourceRevisionIds", "sourcePdfSha256", "assetTreeSha256", "uploadSourceFingerprint",
            "changedChapters", "cancelledJobs", "appliedAt", "restoredAt", "restoredChapters",
            "confirmedMaterialKind",
        }
        if not isinstance(snapshot, dict) or not set(snapshot).issubset(allowed):
            raise LegacyTextbookCleanupError("backup snapshot 包含未审核字段")
        chapter_keys = {"chapterId", "priorRecordVersion", "deletedRecordVersion", "priorStatus", "priorDeletedAt", "priorChapterFingerprint"}
        job_keys = {"jobId", "priorStatus", "priorCancelRequested", "priorLeaseOwner", "priorLeaseExpiresAt"}
        if any(not isinstance(item, dict) or not set(item).issubset(chapter_keys) for item in snapshot.get("changedChapters", [])):
            raise LegacyTextbookCleanupError("backup chapter CAS 字段无效")
        if any(not isinstance(item, dict) or not set(item).issubset(job_keys) for item in snapshot.get("cancelledJobs", [])):
            raise LegacyTextbookCleanupError("backup job CAS 字段无效")

    @classmethod
    def _validate_backup(
        cls,
        backup: dict[str, Any],
        manifest: dict[str, Any],
        manifest_hash: str,
        environment_fingerprint: str,
    ) -> None:
        if set(backup) != {
            "schemaVersion", "manifestId", "manifestSha256", "targetEnvironmentFingerprint",
            "createdAt", "snapshots", "snapshotSha256",
        } or backup.get("schemaVersion") != 1:
            raise LegacyTextbookCleanupError("backup schema 无效")
        if backup.get("manifestId") != manifest["manifestId"] or backup.get("manifestSha256") != manifest_hash:
            raise LegacyTextbookCleanupError("已存在的 backup 属于其他 manifest")
        if backup.get("targetEnvironmentFingerprint") != environment_fingerprint:
            raise LegacyTextbookCleanupError("已存在的 backup 属于其他目标环境")
        if backup.get("snapshotSha256") != _digest(backup.get("snapshots")):
            raise LegacyTextbookCleanupError("backup snapshot SHA-256 校验失败")
        if not isinstance(backup.get("snapshots"), list) or len(backup["snapshots"]) != 1:
            raise LegacyTextbookCleanupError("backup 必须含唯一 upload snapshot")
        cls._validate_snapshot_shape(backup["snapshots"][0])


__all__ = [
    "LegacyTextbookCleanupError",
    "LegacyTextbookCleanupService",
    "manifest_sha256",
    "target_environment",
]
