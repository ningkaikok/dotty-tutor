"""Durable upload state access separated from HTTP route orchestration."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from observability import log_event


class UploadRegistry:
    def __init__(
        self,
        *,
        store: Any,
        lesson_store: dict[str, dict[str, Any]],
        default_guide_cards: list[dict[str, Any]],
        pdf_tail_check_bytes: int,
    ) -> None:
        self.store = store
        self.lesson_store = lesson_store
        self.default_guide_cards = default_guide_cards
        self.pdf_tail_check_bytes = pdf_tail_check_bytes
        self.uploads: dict[str, dict[str, Any]] = {}

    def get(self, upload_id: str) -> dict[str, Any]:
        job = self.uploads.get(upload_id)
        if not job:
            job = self.store.load_job(upload_id)
            if job:
                self.uploads[upload_id] = job
                for batch_id, payload in job.get("batchPayloads", {}).items():
                    self.lesson_store[payload["question"]["id"]] = {
                        "payload": payload,
                        "guideCards": job.get("batchGuideCards", {}).get(batch_id) or self.default_guide_cards,
                    }
        if not job:
            raise HTTPException(status_code=404, detail="上传任务不存在")
        return job

    def status(self, job: dict[str, Any]) -> dict[str, Any]:
        uploaded = sorted(
            int(path.stem.split("-")[-1])
            for path in job["directory"].glob("chunk-*.part")
        )
        result = {
            "uploadId": job["uploadId"],
            "filename": job["filename"],
            "size": job["size"],
            "chunkSize": job["chunkSize"],
            "totalChunks": job["totalChunks"],
            "uploadedChunks": uploaded,
            "status": job["status"],
            "progress": job.get("progress", 0),
            "message": job.get("message", ""),
            "elapsedSeconds": round(
                ((job.get("completedAt") or time.time()) - job["startedAt"]),
                1,
            ),
        }
        if job.get("result"):
            result["result"] = job["result"]
        return result

    def update(self, job: dict[str, Any], status: str, progress: int, message: str) -> None:
        previous_status = job.get("status")
        job["status"] = status
        job["progress"] = max(0, min(progress, 100))
        job["message"] = message
        job["updatedAt"] = time.time()
        self.store.save_job(job)
        log_event(
            "upload.status.changed" if previous_status != status else "upload.progress",
            level=20 if previous_status != status else 10,
            upload_id=job.get("uploadId"),
            status=status,
            progress=job.get("progress", 0),
        )

    def validate_pdf_envelope(self, path: Path) -> None:
        """Reject truncated/non-PDF files before handing them to a parser."""
        with path.open("rb") as pdf_file:
            header = pdf_file.read(8)
            pdf_file.seek(max(0, path.stat().st_size - self.pdf_tail_check_bytes))
            tail = pdf_file.read()

        if not header.startswith(b"%PDF-"):
            raise ValueError("文件头不是有效的 PDF（缺少 %PDF- 标记）")
        if b"%%EOF" not in tail:
            size_mb = path.stat().st_size / 1024 / 1024
            raise ValueError(
                "文件缺少 PDF 结束标记（%%EOF）。"
                f"分块已完整合并为 {size_mb:.1f} MB，因此原 PDF 很可能下载不完整或导出中断；"
                "请重新下载，或用系统的‘打印 → 存储为 PDF’生成新文件后重试"
            )
