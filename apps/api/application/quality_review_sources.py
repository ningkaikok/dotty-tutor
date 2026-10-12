"""Resolve only approved OpenStax PDFs and verify page-bound review evidence."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from application.services.chapter_source_preview import file_hash, source_file
from infrastructure.runtime.ocr_runtime import runtime as ocr_runtime

MANAGED_SOURCE_ASSETS: dict[str, str] = {
    "openstax-elementary-algebra-2e": "ElementaryAlgebra2e-WEB.pdf",
    "openstax-writing-guide": "WritingGuide-WEB.pdf",
}
MAX_SOURCE_PDF_BYTES = 80 * 1024 * 1024


class QualityReviewSourceAssets:
    """Read only known configured files or originals already stored by TextbookStore."""

    def __init__(self, *, source_root: Path, data_root: Path, source_store: Any | None = None) -> None:
        self.source_root = source_root.expanduser().absolute()
        self.data_root = data_root.expanduser().resolve()
        self.source_store = source_store

    def resolve(self, source: dict[str, Any], page_number: int) -> dict[str, Any]:
        """Resolve a source by managed upload ID or a fixed server-configured asset name."""
        filename = ""
        upload_directory: Path | None = None
        path: Path
        upload_id = source.get("uploadId")
        if isinstance(upload_id, str) and upload_id.strip() and self.source_store is not None:
            job = self.source_store.load_job(upload_id)
            if not job or job.get("status") not in {"complete", "completed", "ready"}:
                raise ValueError("来源上传任务不可用或尚未完成")
            resolved_path = source_file(self.source_store, job)
            if resolved_path is None:
                raise ValueError("来源上传任务不可用或尚未完成")
            path = resolved_path
            upload_directory = Path(job["directory"]).resolve()
            filename = str(job.get("filename") or "source.pdf")
        else:
            asset_id = str(source.get("sourceAssetId", ""))
            filename = MANAGED_SOURCE_ASSETS.get(asset_id, "")
            if not filename:
                raise ValueError("该来源没有受管上传 ID 或已配置素材编号")
            if self.source_root.is_symlink():
                raise ValueError("教材素材目录不能是符号链接")
            try:
                root = self.source_root.resolve(strict=True)
            except OSError as error:
                raise ValueError("服务端配置的教材素材目录不可用") from error
            if not root.is_dir():
                raise ValueError("教材素材目录不可用")
            candidate = root / filename
            if candidate.is_symlink():
                raise ValueError("受管教材素材不能是符号链接")
            try:
                path = candidate.resolve(strict=True)
            except OSError as error:
                raise ValueError("受管教材素材文件不可用") from error
            if root not in path.parents or not path.is_file():
                raise ValueError("受管教材素材不在允许目录中")
        size = path.stat().st_size
        if size <= 0 or size > MAX_SOURCE_PDF_BYTES:
            raise ValueError("受管教材 PDF 大小超出限制")
        # A cached (mtime, size) shortcut can miss replacement with preserved
        # metadata, so every decision and preview binds to a fresh content hash.
        fingerprint = file_hash(path)
        declared_hash = str(source.get("contentSha256", "")).lower()
        if declared_hash and declared_hash != fingerprint:
            raise ValueError("服务端教材 PDF 指纹与来源包声明不符")
        if not declared_hash:
            source["contentSha256"] = fingerprint
        reader = PdfReader(str(path), strict=False)
        if page_number < 1 or page_number > len(reader.pages):
            raise ValueError("教材原页码超出 PDF 页面范围")
        return {"path": path, "filename": filename, "page": page_number, "reader": reader,
                "fingerprint": fingerprint, "uploadDirectory": upload_directory}

    def render_preview(self, source_page: dict[str, Any]) -> Path:
        """Render one bounded PDF page into a private cache, rechecking source identity."""
        path = Path(source_page["path"])
        fingerprint = str(source_page["fingerprint"])
        upload_directory = source_page.get("uploadDirectory")
        if isinstance(upload_directory, Path):
            cache_root = upload_directory / "assets" / "quality-review-previews" / fingerprint
            allowed_root = upload_directory.resolve()
        else:
            cache_root = self.data_root / "quality-review" / "previews" / fingerprint
            allowed_root = (self.data_root / "quality-review" / "previews").resolve()
        cache_root.mkdir(parents=True, exist_ok=True)
        resolved_cache = cache_root.resolve()
        if not resolved_cache.is_relative_to(allowed_root):
            raise ValueError("原页预览缓存超出受管数据目录")
        if file_hash(path) != fingerprint:
            raise ValueError("渲染期间受管教材文件已变化")
        rendered = ocr_runtime.render_page_image(path, int(source_page["page"]), resolved_cache, "")
        if not rendered:
            raise ValueError("原页渲染失败；不会使用 OCR 文本代替原图")
        if file_hash(path) != fingerprint:
            raise ValueError("原页渲染期间教材文件发生变化")
        raster = resolved_cache / f"rendered-page-{int(source_page['page']):04d}.png"
        if not raster.is_file() or raster.resolve().parent != resolved_cache or raster.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("原页预览产物不可用或大小超出限制")
        return raster


def verify_page_quote(source_page: dict[str, Any], quote: str) -> str:
    """Require the reviewer quote to match text extracted from the pinned page."""
    normalized_quote = _normalize(quote)
    if not normalized_quote:
        raise ValueError("最终教材验收需要填写原页核对摘录")
    page_text = source_page["reader"].pages[source_page["page"] - 1].extract_text() or ""
    if normalized_quote not in _normalize(page_text):
        raise ValueError("核对摘录未在该教材原页找到；请重新检查页码和引用")
    return normalized_quote


def verify_locator_quote(source_page: dict[str, Any], locator: dict[str, Any]) -> None:
    """Stop review if the imported locator no longer exists on its pinned PDF page."""
    quote = locator.get("quote")
    if not isinstance(quote, str) or not _normalize(quote) or _normalize(quote) not in _normalize(
        source_page["reader"].pages[source_page["page"] - 1].extract_text() or ""
    ):
        raise ValueError("来源定位引用未在对应教材原页找到")


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


__all__ = ["MANAGED_SOURCE_ASSETS", "QualityReviewSourceAssets", "verify_locator_quote", "verify_page_quote"]
