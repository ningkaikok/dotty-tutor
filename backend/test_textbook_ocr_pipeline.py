"""教材页面 OCR 编排的集成边界测试。"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from textbook_ocr_pipeline import resolve_routed_ocr_source


class _Images:
    def __init__(self, count: int) -> None:
        self._count = count

    def __len__(self) -> int:
        return self._count


class _Page:
    def __init__(self, text: str, image_count: int = 0) -> None:
        self._text = text
        self.images = _Images(image_count)

    def extract_text(self) -> str:
        return self._text


class _Runtime:
    def __init__(self, provider: str = "auto", mineru_available: bool = True) -> None:
        self.selection = SimpleNamespace(provider=provider)
        self._mineru_available = mineru_available
        self.parse_calls: list[tuple[int, int | None]] = []

    def mineru_command(self) -> Path | None:
        return Path("/fake/mineru") if self._mineru_available else None

    def parse(
        self,
        _source_path: Path,
        start_page: int,
        end_page: int | None,
        _asset_dir: Path,
        _asset_url_prefix: str,
    ) -> tuple[str, dict]:
        self.parse_calls.append((start_page, end_page))
        return f"{start_page + 1}、计算 $x+1$。", {
            "requestedProvider": self.selection.provider,
            "provider": "mineru",
            "mode": "test",
            "fallback": False,
            "output": "markdown",
            "imageUrls": [],
        }


class TextbookOcrPipelineTests(unittest.TestCase):
    def _resolve(self, directory: str, runtime: _Runtime, pages: list[_Page]):
        root = Path(directory)
        source = root / "source.pdf"
        source.write_bytes(b"%PDF-test")
        with patch(
            "textbook_ocr_pipeline.PdfReader",
            return_value=SimpleNamespace(pages=pages),
        ):
            return resolve_routed_ocr_source(
                runtime=runtime,
                source_text="",
                source_path=source,
                start_page=0,
                end_page=len(pages) - 1,
                asset_dir=root / "assets",
                asset_url_prefix="/assets",
                cache_dir=root / "cache",
                content_hash="a" * 64,
            )

    def test_auto_routes_only_scanned_page_to_mineru(self) -> None:
        with TemporaryDirectory() as directory:
            runtime = _Runtime()
            long_text = "普通电子教材文字层。" * 40
            source, run = self._resolve(directory, runtime, [
                _Page(long_text),
                _Page("", image_count=1),
                _Page(long_text),
            ])
            self.assertEqual(runtime.parse_calls, [(1, 1)])
            self.assertEqual(
                [route["provider"] for route in run["pageRoutes"]],
                ["pypdf", "mineru", "pypdf"],
            )
            self.assertEqual(run["provider"], "hybrid")
            self.assertIn("2、计算", source)

    def test_cache_reuses_provider_output(self) -> None:
        with TemporaryDirectory() as directory:
            runtime = _Runtime(provider="mineru")
            self._resolve(directory, runtime, [_Page("")])
            _source, second_run = self._resolve(directory, runtime, [_Page("")])
            self.assertEqual(runtime.parse_calls, [(0, 0)])
            self.assertTrue(second_run["cacheHit"])

    def test_explicit_pypdf_never_upgrades_empty_page(self) -> None:
        with TemporaryDirectory() as directory:
            runtime = _Runtime(provider="pypdf")
            _source, run = self._resolve(directory, runtime, [_Page("", image_count=1)])
            self.assertEqual(runtime.parse_calls, [])
            self.assertEqual(run["pageRoutes"][0]["provider"], "pypdf")
            self.assertEqual(run["quality"][0]["status"], "retry")


if __name__ == "__main__":
    unittest.main()
