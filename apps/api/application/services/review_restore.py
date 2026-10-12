"""Idempotent maintenance import of source-verified pending historical review packets."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import text

from domain.contracts.quality_review import QualityReviewPackageImport


def prepare_restore_packet(rows: list[dict[str, Any]], *, title: str) -> dict[str, Any]:
    """Discard filesystem transport fields; never translate archived decisions into approval."""
    cases = []
    for row in rows:
        if row.get("counted") is True or row.get("reviewer") or row.get("reviewStatus") not in (None, "pending_human_review"):
            raise ValueError("此恢复入口只接收待真人复核案例；已审记录须保留原审计而不能重建")
        source = {key: value for key, value in row["source"].items() if key not in {"file", "path", "sourceFilePath"}}
        cases.append({key: row[key] for key in ("id", "subject", "sourcePage", "sourceLocator", "input", "expected", "actual") if key in row} | {"source": source})
    payload = QualityReviewPackageImport.model_validate({"title": title, "cases": cases}).model_dump()
    key = hashlib.sha256(json.dumps(payload["cases"], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    payload["source"] = {"restoreSnapshotSha256": key}
    return payload


def restore_pending_package(service: Any, payload: dict[str, Any], actor: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Serialize equivalent imports; an existing packet keeps all decisions and versions."""
    key = payload["source"]["restoreSnapshotSha256"]
    actual = hashlib.sha256(json.dumps(payload["cases"], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    if actual != key:
        raise ValueError("恢复指纹与案例内容不匹配")
    with service.store.atomic() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(hashtext(:restore_key))"), {"restore_key": "review-restore:" + key})
        found = next((package for package in service.store.list_packages() if package["source"].get("restoreSnapshotSha256") == key), None)
        if found:
            return found, False
        return service.import_package(payload, actor), True
