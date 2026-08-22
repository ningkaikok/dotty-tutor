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
        self.render_calls: list[int] = []

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

    def render_page_image(
        self,
        _source_path: Path,
        page_number: int,
        asset_dir: Path,
        asset_url_prefix: str,
    ) -> tuple[str, str] | None:
        self.render_calls.append(page_number)
        asset_dir.mkdir(parents=True, exist_ok=True)
        (asset_dir / "rendered-page-0001.png").write_bytes(b"png")
        return "images/rendered-page-0001.png", f"{asset_url_prefix}/rendered-page-0001.png"


class TextbookOcrPipelineTests(unittest.TestCase):
    def _resolve(self, directory: str, runtime: _Runtime, pages: list[_Page], refresh: bool = False):
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
                refresh=refresh,
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

    def test_refresh_bypasses_cached_provider_output(self) -> None:
        with TemporaryDirectory() as directory:
            runtime = _Runtime(provider="mineru")
            self._resolve(directory, runtime, [_Page("")])
            _source, refreshed_run = self._resolve(directory, runtime, [_Page("")], refresh=True)
            self.assertEqual(runtime.parse_calls, [(0, 0), (0, 0)])
            self.assertFalse(refreshed_run["cacheHit"])

    def test_auto_mode_skips_mineru_upgrade_for_blank_pages(self) -> None:
        """预检参与路由：空白页（无文字层且无图片）不值得花费版面 OCR 成本。

        扫描页（有图片、无文字）仍必须升级到 MinerU——预检绝不能把有内容的
        页面当成空白页跳过。
        """
        with TemporaryDirectory() as directory:
            runtime = _Runtime()
            long_text = "普通电子教材文字层。" * 40
            _source, run = self._resolve(directory, runtime, [
                _Page(""),
                _Page("", image_count=1),
                _Page(long_text),
            ])
            # 只有扫描页进入 MinerU；真正的空白页留在 pypdf 路径。
            self.assertEqual(runtime.parse_calls, [(1, 1)])
            routes = run["pageRoutes"]
            self.assertEqual([route["provider"] for route in routes], ["pypdf", "mineru", "pypdf"])
            self.assertTrue(routes[0]["preflight"]["upgradeSkippedByPreflight"])
            preflight = run["preflight"]
            self.assertEqual(preflight["totalPages"], 3)
            self.assertEqual(preflight["blankPages"], [1])
            self.assertEqual(preflight["visualOcrNeededPages"], [2])
            self.assertEqual(preflight["processablePages"], 2)

    def test_explicit_pypdf_never_upgrades_empty_page(self) -> None:
        with TemporaryDirectory() as directory:
            runtime = _Runtime(provider="pypdf")
            _source, run = self._resolve(directory, runtime, [_Page("", image_count=1)])
            self.assertEqual(runtime.parse_calls, [])
            self.assertEqual(run["pageRoutes"][0]["provider"], "pypdf")
            self.assertEqual(run["quality"][0]["status"], "retry")

    def test_visual_text_gets_rendered_page_image(self) -> None:
        with TemporaryDirectory() as directory:
            runtime = _Runtime(provider="pypdf")
            source, run = self._resolve(
                directory,
                runtime,
                [_Page("3．某几何体的左视图如图所示。")],
            )
            self.assertEqual(runtime.render_calls, [1])
            self.assertIn("images/rendered-page-0001.png", source)
            self.assertEqual(run["imageUrls"], ["/assets/rendered-page-0001.png"])


if __name__ == "__main__":
    unittest.main()
