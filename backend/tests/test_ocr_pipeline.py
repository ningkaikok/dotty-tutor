"""页面 OCR 路由与中间结果缓存的单元测试。"""

from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from ocr_pipeline import OcrResultCache, build_ocr_cache_key, choose_ocr_provider, pdf_content_hash, probe_page


class OcrPipelineTests(unittest.TestCase):
    def test_text_page_routes_to_pypdf(self) -> None:
        probe = probe_page("这是具有足够文字层的普通阅读材料。" * 30)
        self.assertEqual(choose_ocr_provider(probe), "pypdf")

    def test_scanned_and_formula_pages_route_to_mineru(self) -> None:
        scanned = probe_page("", has_full_page_image=True)
        formula = probe_page(r"计算 $\\frac{a+b}{c}=\\sqrt{d}$ 的值。")
        self.assertEqual(choose_ocr_provider(scanned), "mineru")
        self.assertEqual(choose_ocr_provider(formula), "mineru")

    def test_cache_hit_and_provider_version_invalidation(self) -> None:
        content_hash = pdf_content_hash(b"same pdf content")
        old_key = build_ocr_cache_key(
            content_hash, start_page=0, end_page=4, provider="mineru", provider_version="1.0"
        )
        updated_key = build_ocr_cache_key(
            content_hash, start_page=0, end_page=4, provider="mineru", provider_version="1.1"
        )
        with TemporaryDirectory() as directory:
            cache = OcrResultCache(Path(directory))
            cache.save(
                old_key,
                markdown="# 第 1 题",
                image_urls=["/assets/question.png"],
                metadata={"provider": "mineru", "version": "1.0", "durationMs": 42},
            )
            hit = cache.load(old_key)
            self.assertIsNotNone(hit)
            assert hit is not None
            self.assertEqual(hit.markdown, "# 第 1 题")
            self.assertEqual(hit.image_urls, ("/assets/question.png",))
            self.assertEqual(hit.metadata["durationMs"], 42)
            self.assertNotEqual(old_key, updated_key)
            self.assertIsNone(cache.load(updated_key))


if __name__ == "__main__":
    unittest.main()
