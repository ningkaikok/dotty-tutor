"""MinerU 结构化来源适配器。

MinerU 的 JSON 字段在不同版本中略有差异，因此本模块只负责宽容读取并输出稳定的
``SourceBlockRef``。上层题目 IR 只引用这里输出的 block id，不再自行猜测块来源。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

CONTENT_LIST_NAME = "source.content_list.json"
MIDDLE_NAME = "source.middle.json"

SOURCE_BLOCK_MISSING = "SOURCE_BLOCK_MISSING"
SOURCE_TEXT_NOT_RECONSTRUCTABLE = "SOURCE_TEXT_NOT_RECONSTRUCTABLE"
PAGE_RANGE_INVALID = "PAGE_RANGE_INVALID"
IMAGE_OWNER_CONFLICT = "IMAGE_OWNER_CONFLICT"
ANSWER_REFERENCE_AMBIGUOUS = "ANSWER_REFERENCE_AMBIGUOUS"
BOUNDARY_LOW_CONFIDENCE = "BOUNDARY_LOW_CONFIDENCE"


@dataclass(frozen=True)
class SourceBlockRef:
    block_id: str
    page: int
    block_type: str
    bbox: tuple[float, float, float, float] | None
    order: int
    text_hash: str
    asset_ids: tuple[str, ...] = ()
    origin: str = "markdown-fallback"
    text: str = ""

    @property
    def type(self) -> str:
        """兼容调用方使用 JSON 字段名 ``type``。"""
        return self.block_type

    def as_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        return {
            "blockId": raw.pop("block_id"),
            "page": raw.pop("page"),
            "type": raw.pop("block_type"),
            "bbox": list(raw.pop("bbox")) if raw.get("bbox") is not None else None,
            "order": raw.pop("order"),
            "textHash": raw.pop("text_hash"),
            "assetIds": list(raw.pop("asset_ids")),
            "origin": raw.pop("origin"),
            **raw,
        }


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _bbox(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        values = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if values[2] <= values[0] or values[3] <= values[1]:
        return None
    return values  # type: ignore[return-value]


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return " ".join(_text(item) for item in value if _text(item))
    if isinstance(value, dict):
        direct = value.get("text") or value.get("content") or value.get("value")
        if direct:
            return _text(direct)
        spans = value.get("spans")
        if isinstance(spans, list):
            return "".join(_text(span) for span in spans)
        return ""
    return ""


def _assets(item: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("img_path", "image_path", "url", "assetId", "asset_id"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            values.append(Path(value).name)
    for key in ("assetIds", "asset_ids", "images"):
        value = item.get(key)
        if isinstance(value, list):
            values.extend(Path(str(item)).name for item in value if str(item).strip())
    return tuple(dict.fromkeys(values))


def _page(item: dict[str, Any], start_page: int) -> int:
    raw = item.get("page_idx", item.get("page", item.get("pageIndex", 0)))
    try:
        return start_page + int(raw)
    except (TypeError, ValueError):
        return start_page


def _stable_id(prefix: str, page: int, order: int, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{page}-{order}-{digest}"


def _content_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("content_list", "blocks", "items", "data"):
            if isinstance(data.get(key), list):
                return [item for item in data[key] if isinstance(item, dict)]
    return []


def load_source_blocks(asset_dir: Path | None, *, start_page: int = 1) -> tuple[SourceBlockRef, ...]:
    """读取 MinerU 块；缺少结构化产物时返回空元组，调用方走明确 fallback。"""
    if asset_dir is None:
        return ()
    content_path = Path(asset_dir) / CONTENT_LIST_NAME
    content_items = _content_items(_json(content_path)) if content_path.is_file() else []
    refs: list[SourceBlockRef] = []
    for order, item in enumerate(content_items):
        text = _text(item.get("text") or item.get("content") or item.get("value"))
        block_type = str(item.get("type") or item.get("block_type") or "unknown")
        page = _page(item, start_page)
        explicit_id = item.get("id") or item.get("block_id") or item.get("blockId")
        block_id = str(explicit_id).strip() if explicit_id else _stable_id("mineru-block", page, order, text)
        refs.append(SourceBlockRef(
            block_id=block_id,
            page=page,
            block_type=block_type,
            bbox=_bbox(item.get("bbox")),
            order=order,
            text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            asset_ids=_assets(item),
            origin="mineru",
            text=text,
        ))

    # middle.json 提供行/段级文本。当 content_list 只有整页一个文本块时，用 middle
    # 段落补齐可反查的细粒度来源，同时保留 content_list 的图片块。
    middle = _json(Path(asset_dir) / MIDDLE_NAME)
    pages = middle.get("pdf_info") if isinstance(middle, dict) else None
    if isinstance(pages, list):
        middle_refs: list[SourceBlockRef] = []
        order = 0
        for page_offset, page_data in enumerate(pages):
            paragraphs = page_data.get("para_blocks") if isinstance(page_data, dict) else None
            if not isinstance(paragraphs, list):
                continue
            for para in paragraphs:
                text = ""
                if isinstance(para, dict):
                    lines = para.get("lines")
                    if isinstance(lines, list):
                        text = "\n".join(_text(line) for line in lines if _text(line))
                    text = text or _text(para)
                if not text:
                    continue
                page = start_page + page_offset
                explicit_id = para.get("id") or para.get("block_id") if isinstance(para, dict) else None
                block_id = str(explicit_id).strip() if explicit_id else _stable_id("mineru-middle", page, order, text)
                middle_refs.append(SourceBlockRef(
                    block_id=block_id,
                    page=page,
                    block_type=str(para.get("type") or "text") if isinstance(para, dict) else "text",
                    bbox=_bbox(para.get("bbox")) if isinstance(para, dict) else None,
                    order=order,
                    text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    origin="mineru-middle",
                    text=text,
                ))
                order += 1
        if middle_refs:
            # content_list carries the authoritative block id. middle.json is used only
            # to fill a paragraph that content_list could not represent, never to replace
            # a real content-list identity.
            content_texts = [re.sub(r"\s+", "", ref.text) for ref in refs if ref.text]
            extra_refs = [
                ref for ref in middle_refs
                if re.sub(r"\s+", "", ref.text) not in content_texts
            ]
            refs = refs + extra_refs
    return tuple(sorted(refs, key=lambda ref: (ref.page, ref.order, ref.block_id)))


def fallback_block(text: str, *, page: int, order: int = 0) -> SourceBlockRef:
    """为无 MinerU 结构时创建显式 markdown-fallback 块。"""
    normalized = str(text or "").strip()
    return SourceBlockRef(
        block_id=_stable_id("markdown-fallback", page, order, normalized),
        page=page,
        block_type="text",
        bbox=None,
        order=order,
        text_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        origin="markdown-fallback",
        text=normalized,
    )


def match_source_blocks(
    question_text: str,
    blocks: tuple[SourceBlockRef, ...],
    *,
    pages: tuple[int, ...],
    assets: tuple[str, ...] = (),
) -> tuple[SourceBlockRef, ...]:
    """按原文/页码/图片证据匹配真实块；不确定时返回空元组。"""
    normalized = re.sub(r"\s+", "", question_text)
    candidates: list[SourceBlockRef] = []
    for ref in blocks:
        if pages and ref.page not in pages:
            continue
        ref_text = re.sub(r"\s+", "", ref.text)
        text_match = bool(ref_text and (ref_text in normalized or normalized[:40] in ref_text))
        asset_match = bool(set(assets) & set(ref.asset_ids))
        if text_match or asset_match:
            candidates.append(ref)
    return tuple(candidates)


@dataclass(frozen=True)
class StructuredSource:
    """稳定的结构化来源快照及读取诊断。"""

    blocks: tuple[SourceBlockRef, ...] = ()
    available: bool = False
    diagnostics: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "blocks": [block.as_dict() for block in self.blocks],
            "diagnostics": list(self.diagnostics),
        }


def load_structured_source(
    asset_dir: Path | None,
    *,
    ocr_start_page: int = 1,
    ocr_end_page: int | None = None,
) -> StructuredSource:
    """读取结构化产物并做页码范围校验。"""
    blocks = load_source_blocks(asset_dir, start_page=ocr_start_page)
    diagnostics: list[dict[str, Any]] = []
    if asset_dir is not None and not (Path(asset_dir) / CONTENT_LIST_NAME).is_file():
        diagnostics.append({"code": SOURCE_TEXT_NOT_RECONSTRUCTABLE, "message": "缺少 MinerU content_list"})
    if ocr_end_page is not None and ocr_end_page < ocr_start_page:
        diagnostics.append({"code": PAGE_RANGE_INVALID, "message": "OCR 页范围无效"})
    for block in blocks:
        if ocr_end_page is not None and not ocr_start_page <= block.page <= ocr_end_page:
            diagnostics.append({"code": PAGE_RANGE_INVALID, "blockId": block.block_id})
    return StructuredSource(blocks=blocks, available=bool(blocks), diagnostics=tuple(diagnostics))


def refs_for_question(question_text: str, source: StructuredSource) -> tuple[SourceBlockRef, ...]:
    pages = tuple(block.page for block in source.blocks)
    assets = tuple(Path(value).name for value in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", question_text))
    return match_source_blocks(question_text, source.blocks, pages=pages, assets=assets)


def merge_diagnostics(*groups: Any) -> list[dict[str, Any]]:
    """合并并按 code/message 去重，便于 API 稳定返回。"""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group or ():
            if isinstance(item, str):
                item = {"code": item, "message": item}
            if not isinstance(item, dict):
                continue
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if key not in seen:
                seen.add(key)
                merged.append(dict(item))
    return merged
