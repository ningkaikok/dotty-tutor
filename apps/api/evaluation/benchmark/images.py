"""Deliver pinned local image bytes; SVGs cannot fetch files or network resources."""
from __future__ import annotations

import hashlib
import io
import math
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from xml.etree import ElementTree

MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 16_000_000
_SVG_ELEMENTS = frozenset({"svg", "g", "path", "text", "tspan", "rect", "circle", "line",
                           "polyline", "polygon", "ellipse", "title", "desc"})


class ImageConversionUnavailable(RuntimeError):
    """Optional native rendering support is missing; this is not a model failure."""


def asset_digest(path: Path) -> str:
    """SVG review hashes use LF so Git's Windows checkout conversion is harmless."""
    data = path.read_bytes()
    return _digest_bytes(data, svg=path.suffix.lower() == ".svg")


def _digest_bytes(data: bytes, *, svg: bool) -> str:
    if svg:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def pinned_asset(case: dict[str, Any], asset_root: Path) -> Path:
    """Resolve a hash-verified asset inside its review packet, never a supplied URL."""
    name = case.get("input", {}).get("imageAsset")
    if not isinstance(name, str) or not name:
        raise ValueError("图像案例缺少本地素材")
    path = (asset_root / name).resolve()
    if not path.is_relative_to(asset_root.resolve()) or not path.is_file():
        raise ValueError("图像素材不存在或不在评测目录内")
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("评测图像超过大小上限")
    if asset_digest(path) != case.get("imageAssetSha256"):
        raise ValueError("评测图像指纹不匹配")
    return path


def _safe_svg(data: bytes) -> tuple[int, int]:
    # Review packets contain simple geometry. A restrictive subset avoids the
    # renderer's URL/file resolution entirely, including CSS and embedded data.
    if b"<!" in data:
        raise ValueError("评测 SVG 不允许声明或实体")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise ValueError("评测 SVG 无效") from error
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise ValueError("评测 SVG 缺少根元素")
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] not in _SVG_ELEMENTS:
            raise ValueError("评测 SVG 包含不允许的元素")
        for key, value in node.attrib.items():
            if key.rsplit("}", 1)[-1].lower() in {"href", "style", "base"} or "url(" in value.lower():
                raise ValueError("评测 SVG 不允许外部资源或样式")
    try:
        if root.get("viewBox"):
            values = [float(part) for part in re.split(r"[\s,]+", root.get("viewBox", "").strip())]
            if len(values) != 4:
                raise ValueError("invalid viewBox")
            width, height = values[2:]
        else:
            width, height = float(root.get("width", "480")), float(root.get("height", "320"))
        if not all(math.isfinite(value) and 0 < value <= 100_000 for value in (width, height)):
            raise ValueError("invalid dimensions")
    except ValueError as error:
        raise ValueError("评测 SVG 尺寸无效") from error
    scale = min(1200 / width, 800 / height)
    return max(1, round(width * scale)), max(1, round(height * scale))


@contextmanager
def prepared_image(case: dict[str, Any], asset_root: Path) -> Iterator[Path]:
    """Normalize a verified image to PNG in an ephemeral directory for one call."""
    from PIL import Image

    source = pinned_asset(case, asset_root)
    data = source.read_bytes()
    if len(data) > MAX_BYTES or _digest_bytes(data, svg=source.suffix.lower() == ".svg") != case.get("imageAssetSha256"):
        raise ValueError("评测图像在读取期间发生变化")
    if source.suffix.lower() == ".svg":
        width, height = _safe_svg(data)
        try:
            import cairosvg
        except (ImportError, OSError) as error:
            raise ImageConversionUnavailable("SVG 转换需要可用的 Cairo 原生库") from error
        rendered = cairosvg.svg2png(bytestring=data, unsafe=False, output_width=width, output_height=height)
        if not isinstance(rendered, bytes):
            raise ValueError("SVG 转换未返回图片字节")
        data = rendered
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > MAX_PIXELS:
            raise ValueError("评测图像像素超过上限")
        image.load()
        with tempfile.TemporaryDirectory(prefix="dotty-eval-image-") as folder:
            target = Path(folder) / "input.png"
            image.convert("RGB").save(target, format="PNG")
            yield target
