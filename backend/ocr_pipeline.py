"""页面级 OCR 路由和可复用的中间结果文件缓存。

这里不调用 OCR Provider。它只根据轻量页面信号给出可解释的选择，并把昂贵 OCR
的输出以内容寻址方式保存。这样题目切分、公式标准化和质量门禁可以共享同一份稳定
输入，而无需在重试时重新执行 MinerU。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping


RoutedProvider = Literal["pypdf", "mineru"]

# 这些信号故意偏保守：普通电子文本优先走更快的文字层；只要很像扫描件或公式页，
# 就交给版面 OCR，避免因为一次错误路由损失题图、上下标或分式结构。
_FORMULA_SIGNAL = re.compile(
    r"(?:\\(?:frac|sqrt|sum|int|begin|left|right|times|div|leq|geq|textbackslash|textcirc|textdegree)|\$|[∑∫√≈≠≤≥])"
)


@dataclass(frozen=True)
class PageProbe:
    """页面探测的最小契约；概率均为 ``0`` 到 ``1`` 的闭区间。"""

    text_length: int
    image_likelihood: float
    formula_likelihood: float

    def __post_init__(self) -> None:
        if self.text_length < 0:
            raise ValueError("text_length 不能为负数")
        for name in ("image_likelihood", "formula_likelihood"):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} 必须介于 0 和 1 之间")

    @property
    def scan_likelihood(self) -> float:
        """估计页面依赖图像版面的程度，不把有插图的长文字页误判为扫描件。"""
        text_scarcity = max(0.0, min(1.0, 1 - self.text_length / 240))
        return round(self.image_likelihood * (0.45 + 0.55 * text_scarcity), 3)


def probe_page(text: str, *, image_count: int = 0, has_full_page_image: bool = False) -> PageProbe:
    """从已读取的文字层和 PDF 图片计数生成确定性页面信号。

    ``image_count`` 只是低成本启发式，不能证明页面是扫描件；所以它会与文字稀少程度
    一起参与路由。调用方可在 PDF 解析器明确识别整页图像时传入 ``has_full_page_image``。
    """
    if image_count < 0:
        raise ValueError("image_count 不能为负数")
    normalized = "".join(text.split())
    image_likelihood = 1.0 if has_full_page_image else min(0.8, image_count * 0.25)
    formula_hits = len(_FORMULA_SIGNAL.findall(text))
    formula_likelihood = min(1.0, formula_hits / 3)
    return PageProbe(len(normalized), image_likelihood, formula_likelihood)


def choose_ocr_provider(
    probe: PageProbe,
    *,
    requested_provider: Literal["auto", "pypdf", "mineru"] = "auto",
    mineru_available: bool = True,
) -> RoutedProvider:
    """为页面选择 OCR Provider，并保留显式用户选择的优先级。

    MinerU 不可用时一律回退 pypdf；上层可记录该回退原因。自动模式中，短文字配合
    图片信号、或明显公式信号，都优先 MinerU，其他电子文本页走 pypdf。
    """
    if requested_provider not in {"auto", "pypdf", "mineru"}:
        raise ValueError(f"不支持的 OCR Provider：{requested_provider}")
    if requested_provider == "pypdf":
        return "pypdf"
    if requested_provider == "mineru":
        return "mineru" if mineru_available else "pypdf"
    if not mineru_available:
        return "pypdf"
    if probe.scan_likelihood >= 0.35 or probe.formula_likelihood >= 0.34:
        return "mineru"
    return "pypdf"


def pdf_content_hash(source: bytes | Path) -> str:
    """返回 PDF 原始字节的 SHA-256，避免同名文件错误共享缓存。"""
    digest = hashlib.sha256()
    if isinstance(source, bytes):
        digest.update(source)
    else:
        with Path(source).open("rb") as file:
            while block := file.read(1024 * 1024):
                digest.update(block)
    return digest.hexdigest()


def build_ocr_cache_key(
    content_hash: str,
    *,
    start_page: int,
    end_page: int | None,
    provider: RoutedProvider,
    provider_version: str,
) -> str:
    """构造稳定且不暴露文件名的缓存键；Provider 升级自动产生新键。"""
    if len(content_hash) != 64 or any(char not in "0123456789abcdef" for char in content_hash.lower()):
        raise ValueError("content_hash 必须是 SHA-256 十六进制摘要")
    if start_page < 0 or (end_page is not None and end_page < start_page):
        raise ValueError("页范围无效")
    if not provider_version.strip():
        raise ValueError("provider_version 不能为空")
    identity = json.dumps({
        "schema": 1,
        "pdf": content_hash.lower(),
        "pages": [start_page, end_page],
        "provider": provider,
        "version": provider_version,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CachedOcrResult:
    """缓存命中时返回的 OCR 输出与运行审计信息。"""

    markdown: str
    image_urls: tuple[str, ...]
    metadata: Mapping[str, Any]


class OcrResultCache:
    """以单个原子 JSON 文件存储一段 OCR 输出的本地缓存。"""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def load(self, key: str) -> CachedOcrResult | None:
        """读取有效缓存；损坏或旧格式条目视为未命中，以便安全重算。"""
        path = self._path(key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema") != 1 or payload.get("key") != key:
                return None
            markdown = payload["markdown"]
            image_urls = payload["imageUrls"]
            metadata = payload["metadata"]
            if not isinstance(markdown, str) or not isinstance(image_urls, list) or not isinstance(metadata, dict):
                return None
            if not all(isinstance(url, str) for url in image_urls):
                return None
            return CachedOcrResult(markdown, tuple(image_urls), metadata)
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def save(
        self,
        key: str,
        *,
        markdown: str,
        image_urls: list[str] | tuple[str, ...],
        metadata: Mapping[str, Any],
    ) -> CachedOcrResult:
        """原子写入结果，确保并发读取者只会看到旧版本或完整新版本。"""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": 1,
            "key": key,
            "markdown": markdown,
            "imageUrls": list(image_urls),
            "metadata": dict(metadata),
        }
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{key}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
                json.dump(payload, temporary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return CachedOcrResult(markdown, tuple(image_urls), dict(metadata))

    def _path(self, key: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("缓存键必须是 SHA-256 十六进制摘要")
        return self.directory / key[:2] / f"{key}.json"
