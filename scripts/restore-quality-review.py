#!/usr/bin/env python3
"""校验、盘点并幂等恢复历史待审包；原件、决策和模型输出不自动修改。"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from application.quality_review_sources import MANAGED_SOURCE_ASSETS  # noqa: E402
from application.services.quality_review import QualityReviewService  # noqa: E402
from application.services.review_restore import (  # noqa: E402
    prepare_restore_packet,
    restore_pending_package,
)
from persistence.quality_review_store import QualityReviewStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path, help="历史 cases.jsonl")
    parser.add_argument("--sources", type=Path, required=True, help="历史 PDF 所在目录")
    parser.add_argument("--data-root", type=Path, required=True, help="明确的目标素材目录")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--title", default="恢复的 OpenStax 待审材料")
    parser.add_argument("--api-url", help="兼容现有本机 API，不切换服务或数据库迁移")
    parser.add_argument("--apply", action="store_true", help="须先备份目标数据库；默认仅盘点")
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.cases.read_text().splitlines() if line.strip()]
    packet = prepare_restore_packet(rows, title=args.title)
    managed = {}
    for case in packet["cases"]:
        asset_id = case["source"]["sourceAssetId"]
        filename = MANAGED_SOURCE_ASSETS[asset_id]
        source = args.sources / filename
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if source.is_symlink() or digest != case["source"]["contentSha256"]:
            raise ValueError("原文件 SHA-256 或受管路径不匹配")
        managed[asset_id] = {"path": str(source.resolve()), "sha256": digest, "filename": filename}
    result = {"caseCount": len(packet["cases"]), "snapshotSha256": packet["source"]["restoreSnapshotSha256"],
              "sourceCaseFile": str(args.cases.resolve()), "targetDataRoot": str(args.data_root.resolve()),
              "sources": managed, "applied": False, "humanReviewed": False, "goldCount": 0}
    if args.apply:
        target = args.data_root / "quality-review" / "sources"
        target.mkdir(parents=True, exist_ok=True)
        for asset in managed.values():
            destination = target / asset["filename"]
            if destination.is_symlink():
                raise ValueError("目标教材不能是符号链接")
            if destination.exists():
                if hashlib.sha256(destination.read_bytes()).hexdigest() != asset["sha256"]:
                    raise ValueError("目标已存在不同教材；不会覆盖")
            else:
                shutil.copyfile(asset["path"], destination)
        if args.api_url:
            parsed = urlsplit(args.api_url)
            if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.path not in {"", "/"}:
                raise ValueError("兼容模式只允许明确的本机 API 根地址")
            headers = {"Content-Type": "application/json"}
            if os.getenv("DOTTY_RESTORE_SESSION_TOKEN"):
                headers["Cookie"] = "dotty_session=" + os.environ["DOTTY_RESTORE_SESSION_TOKEN"]
            def request(path, body=None):
                data = json.dumps(body).encode() if body is not None else None
                with urlopen(Request(args.api_url.rstrip("/") + path, data=data, headers=headers), timeout=60) as response:
                    return json.load(response)
            # Same material directory serializes repeated invocations while the old
            # API lacks a transactional restore command. Never overwrite its reviews.
            with (target / ".restore.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                package = next((item for item in request("/api/quality-reviews/packages")["items"]
                    if item["source"].get("restoreSnapshotSha256") == packet["source"]["restoreSnapshotSha256"]), None)
                created = package is None
                if package is None:
                    package = request("/api/quality-reviews/packages", packet)
        else:
            store = QualityReviewStore(data_root=args.data_root)
            try:
                service = QualityReviewService(store, source_root=target)
                package, created = restore_pending_package(service, packet,
                    {"actorId": "maintenance-restore", "actorKind": "maintenance", "identityVerified": False})
            finally:
                store.close()
        result.update(applied=True, created=created, packageId=package["packageId"], pendingCount=package.get("pending", len(packet["cases"])))
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("caseCount", "applied", "goldCount")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
