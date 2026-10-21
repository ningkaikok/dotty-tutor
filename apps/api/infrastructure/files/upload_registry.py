"""Durable upload state access separated from HTTP route orchestration."""

from __future__ import annotations

import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import unquote

from fastapi import HTTPException

from observability import log_event


def safe_path_segment(value: str) -> str | None:
    """仅当 ``value`` 恰好是一个相对路径片段时返回它，否则返回 ``None``。

    路由参数已经被 ASGI 服务器解码过一次，但多重编码仍可能把 ``%2e%2e`` 带到
    这里，所以校验前反复解码到稳定。这里不用 ``Path(value).name`` 兜底：``Path('..').name``
    仍然是 ``'..'``，压平不了路径穿越；而反斜杠在 POSIX 上又不是分隔符，只看
    一种平台语义会得出不同结论。因此显式拒绝 ``.``/``..``、两种分隔符、盘符与
    NUL，并要求 POSIX 与 Windows 两种语义下的 ``name`` 都等于原值。
    """
    candidate = value
    # 多重编码可能把 ``..`` 藏好几层；解到稳定为止（限次，避免构造恶意无限编码）。
    for _ in range(3):
        decoded = unquote(candidate)
        if decoded == candidate:
            break
        candidate = decoded
    if not candidate or candidate in {".", ".."} or "\x00" in candidate:
        return None
    if "/" in candidate or "\\" in candidate:
        return None
    if PurePosixPath(candidate).name != candidate or PureWindowsPath(candidate).name != candidate:
        return None
    if PureWindowsPath(candidate).drive:
        return None
    return candidate


def resolve_inside_upload_root(root: Path, *segments: str) -> Path | None:
    """把校验过的片段拼到可信根上，并确认解析结果仍在根目录内。

    根目录来自服务端上传记录（``job["directory"]``），不是请求参数；越界判定用
    ``resolve()`` 之后的结果与可信根比较，因此符号链接或路径穿越都无法逃逸。
    """
    resolved_root = root.resolve()
    candidate = resolved_root
    for segment in segments:
        safe = safe_path_segment(segment)
        if safe is None:
            return None
        candidate = candidate / safe
    resolved = candidate.resolve()
    return resolved if resolved.is_relative_to(resolved_root) else None


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
        # API and Worker are separate processes. Always refresh the cached snapshot so
        # status polling observes Worker progress and terminal errors from PostgreSQL.
        cached = self.uploads.get(upload_id)
        # A persisted tombstone may have been applied by another process since this
        # worker last refreshed. Failing closed on DB errors prevents stale cached
        # question payloads from reappearing in the library.
        job = self.store.load_job(upload_id)
        if job is None:
            # A missing durable row must not be resurrected from a stale process cache.
            self.uploads.pop(upload_id, None)
        if job:
            if cached:
                for payload in cached.get("batchPayloads", {}).values():
                    question = payload.get("question") if isinstance(payload, dict) else None
                    question_id = question.get("id") if isinstance(question, dict) else None
                    existing = self.lesson_store.get(str(question_id)) if question_id else None
                    if existing and existing.get("payload") == payload:
                        self.lesson_store.pop(str(question_id), None)
            for key, item in list(self.lesson_store.items()):
                if item.get("sourceUploadId") == upload_id:
                    self.lesson_store.pop(key, None)
            self.uploads[upload_id] = job
            for source_question_key, payload in job.get("batchPayloads", {}).items():
                self.lesson_store[payload["question"]["id"]] = {
                    "payload": payload,
                    "guideCards": job.get("batchGuideCards", {}).get(source_question_key) or self.default_guide_cards,
                    "sourceUploadId": upload_id,
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
