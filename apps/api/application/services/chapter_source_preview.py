"""Pinned original-page previews using the existing OCR raster adapter."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from infrastructure.runtime.ocr_runtime import runtime as ocr_runtime


def source_file(store: Any, job: dict[str, Any]) -> Path | None:
    """Never interpret request filenames or serve assets outside the upload root."""
    root = Path(store.upload_root).resolve()
    directory = Path(job["directory"]).resolve()
    path = (directory / "source.pdf").resolve()
    if not directory.is_relative_to(root) or path.parent != directory or not path.is_file():
        return None
    return path


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ChapterSourcePreview:
    """Resolve only pages belonging to a pinned chapter source revision."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def get(self, chapter_id: str, revision_id: str, page: int) -> Path:
        chapter = self.store.load_chapter(chapter_id)
        revision = next((item for item in (chapter or {}).get("sourceRevisions", [])
                         if item["sourceRevisionId"] == revision_id), None)
        if not revision or page not in {item["page"] for item in revision["pages"]}:
            raise LookupError("来源修订或页面不存在")
        if not revision.get("uploadId") or not revision.get("sourceFileSha256"):
            raise LookupError("该来源没有可预览的教材原文件")
        job = self.store.load_job(revision["uploadId"])
        path = source_file(self.store, job) if job and job.get("status") in {"complete", "completed", "ready"} else None
        if path is None:
            raise LookupError("教材原文件不可用")
        expected_hash = revision["sourceFileSha256"]
        if file_hash(path) != expected_hash:
            raise ValueError("教材原文件已变化，不能回写历史来源；请导入新的来源修订")
        try:
            if page < 1 or page > len(PdfReader(path).pages):
                raise LookupError("页码超出教材原文件范围")
        except LookupError:
            raise
        except Exception as error:
            raise ValueError("教材原文件无法读取，请核对 PDF") from error
        # Content identity isolates cached renders from replaced or revised uploads.
        asset_dir = Path(job["directory"]).resolve() / "assets" / "chapter-previews" / expected_hash
        if not asset_dir.resolve().is_relative_to(Path(job["directory"]).resolve()):
            raise ValueError("预览缓存目录超出上传文件边界")
        try:
            rendered = ocr_runtime.render_page_image(path, page, asset_dir, "")
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError("原页渲染失败，可重试；不使用 OCR 文本冒充原图") from error
        if not rendered:
            raise ValueError("当前环境无法渲染原页，请检查现有 PDF 渲染依赖")
        if file_hash(path) != expected_hash:
            raise ValueError("渲染期间教材原文件发生变化，请重新导入来源")
        raster = asset_dir / f"rendered-page-{page:04d}.png"
        if not raster.is_file() or raster.resolve().parent != asset_dir.resolve():
            raise LookupError("原页预览不可用")
        return raster
