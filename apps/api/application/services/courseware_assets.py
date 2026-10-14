"""Controlled crops from frozen source coordinates, never model-invented boxes."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import struct
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from domain.contracts.chapter import ChapterRegion
from domain.contracts.courseware import CoursewareSourceRef


def source_assets(source: dict[str, Any], pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Catalogue whole pages plus regions whose saved normalized coordinates validate."""
    if not source.get("uploadId") or not source.get("sourceFileSha256"):
        return []
    revision = str(source["sourceRevisionId"])
    assets = []
    for page in pages:
        number = int(page["page"])
        common = {"origin": "textbook", "license": source.get("license"),
                  "attributionText": source.get("attributionText"),
                  "distributionScope": source.get("distributionScope") or "private",
                  "sourceFileSha256": source["sourceFileSha256"], "status": "available"}
        regions = []
        for raw in page.get("regions", []):
            try:
                region = ChapterRegion.model_validate(raw)
            except (ValidationError, TypeError):
                continue
            if region.regionId:
                regions.append(region)
        page_id = f"source-page-{revision[:12]}-{number}"
        assets.append({**common, "assetId": page_id, "kind": "source_page",
                       "sourceRefs": [{"sourceRevisionId": revision, "page": number, "assetId": page_id}],
                       "alt": f"教材原页 {number}",
                       "fallbackReason": None if regions else "missing_trusted_region"})
        for region in regions:
            rect = region.model_dump(include={"x", "y", "width", "height"})
            digest = hashlib.sha256(json.dumps([revision, number, region.regionId, rect], sort_keys=True).encode()).hexdigest()[:24]
            asset_id = f"source-region-{digest}"
            assets.append({**common, "assetId": asset_id, "kind": "source_image", "crop": rect,
                           "sourceRegionId": region.regionId,
                           "sourceRefs": [{"sourceRevisionId": revision, "page": number,
                                           "regionIds": [region.regionId], "assetId": asset_id}],
                           "alt": f"教材原页 {number} 区域 {region.regionId}"})
    for asset in assets:
        asset["sourceRefs"] = [CoursewareSourceRef.model_validate(ref).model_dump() for ref in asset["sourceRefs"]]
    return assets


def crop_source_asset(path: Path, source: dict[str, Any], page_number: int, asset: dict[str, Any]) -> Path:
    """Re-derive the crop from the pinned source before serving a bounded PNG cache."""
    page = next((page for page in source.get("pages", []) if page.get("page") == page_number), None)
    if page is None:
        raise ValueError("区域原页不存在")
    expected = next((item for item in source_assets(source, [page]) if item["assetId"] == asset["assetId"]), None)
    if (not expected or expected.get("kind") != "source_image"
            or any(asset.get(key) != expected.get(key) for key in ("crop", "sourceRegionId", "sourceFileSha256", "sourceRefs"))):
        raise ValueError("区域坐标或来源身份与冻结教材不一致")
    content = path.read_bytes()
    if len(content) < 24 or not content.startswith(b"\x89PNG\r\n\x1a\n") or len(content) > 20 * 1024 * 1024:
        raise ValueError("裁图原页必须是受控 PNG")
    width, height = struct.unpack(">II", content[16:24])
    if not (0 < width <= 10000 and 0 < height <= 10000 and width * height <= 40_000_000):
        raise ValueError("裁图原页尺寸超出限制")
    rect = expected["crop"]
    x, y = math.floor(rect["x"] * width), math.floor(rect["y"] * height)
    right, bottom = math.ceil((rect["x"] + rect["width"]) * width), math.ceil((rect["y"] + rect["height"]) * height)
    cropped_width, cropped_height = right - x, bottom - y
    cache_key = hashlib.sha256(content + json.dumps(rect, sort_keys=True).encode()).hexdigest()
    target = path.parent / f"region-{cache_key}.png"
    if target.is_symlink():
        raise ValueError("区域缓存不能是符号链接")
    if not target.exists():
        # Imported here: cairocffi loads native Cairo on import, and only cropping needs it.
        import cairosvg

        encoded = base64.b64encode(content).decode("ascii")
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{cropped_width}" height="{cropped_height}" '
               f'viewBox="{x} {y} {cropped_width} {cropped_height}"><image width="{width}" height="{height}" '
               f'href="data:image/png;base64,{encoded}"/></svg>')
        data = cairosvg.svg2png(bytestring=svg.encode(), unsafe=False)
        if not isinstance(data, bytes):
            raise ValueError("区域 PNG 渲染失败")
        # A concurrent request must never observe a partially written PNG.
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".png", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return target
