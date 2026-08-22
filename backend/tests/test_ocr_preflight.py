"""页面预检分类器与脏页摘要的单元测试。

预检是纯函数：同样的文字层和图片计数必须得到完全相同的类别和原因，
这是"误判样本可以带原因进入 Badcase 登记簿"的前提。
"""

from __future__ import annotations

import unittest

from ocr_preflight import (
    CATEGORY_BLANK,
    CATEGORY_FORMULA_DENSE,
    CATEGORY_IMAGE_MIXED,
    CATEGORY_PUBLICATION,
    CATEGORY_QUESTION_LIKELY,
    CATEGORY_TEXT_ONLY,
    classify_page,
    summarize_preflight,
)


class ClassifyPageTests(unittest.TestCase):
    def test_blank_page_has_no_text_and_no_images(self) -> None:
        result = classify_page("", image_count=0, formula_likelihood=0.0)
        self.assertEqual(result["category"], CATEGORY_BLANK)

    def test_image_only_page_is_not_blank_but_needs_visual_ocr(self) -> None:
        """扫描页有可提取内容，绝不能当空白页跳过——这是预检参与路由的安全边界。"""
        result = classify_page("", image_count=3, formula_likelihood=0.0)
        self.assertEqual(result["category"], CATEGORY_IMAGE_MIXED)
        self.assertTrue(result["needsVisualOcr"])

    def test_publication_info_page_detected_by_strong_markers(self) -> None:
        text = "ISBN 978-7-107-34285-2\n版权所有，侵权必究\n定价 12.50 元\n印次 2026 年第 3 次"
        result = classify_page(text, image_count=0, formula_likelihood=0.0)
        self.assertEqual(result["category"], CATEGORY_PUBLICATION)

    def test_formula_dense_page_detected_by_signal_density(self) -> None:
        text = "已知函数 $f(x) = \\frac{1}{x}$，求 $\\sqrt{x}$ 的取值范围。" * 10
        result = classify_page(text, image_count=0, formula_likelihood=0.8)
        self.assertEqual(result["category"], CATEGORY_FORMULA_DENSE)

    def test_text_page_with_images_is_mixed(self) -> None:
        text = "如图所示，两个三角形全等。" + "普通文字内容。" * 30
        result = classify_page(text, image_count=1, formula_likelihood=0.0)
        self.assertEqual(result["category"], CATEGORY_IMAGE_MIXED)
        self.assertFalse(result["needsVisualOcr"])

    def test_question_number_density_marks_question_page(self) -> None:
        text = "5. 计算下列各题。\n6. 如图，判断正误。\n7. 化简。"
        result = classify_page(text, image_count=0, formula_likelihood=0.0)
        self.assertEqual(result["category"], CATEGORY_QUESTION_LIKELY)

    def test_plain_prose_falls_back_to_text_only(self) -> None:
        text = "这是一段没有任何强特征的普通教材叙述文字。" * 5
        result = classify_page(text, image_count=0, formula_likelihood=0.0)
        self.assertEqual(result["category"], CATEGORY_TEXT_ONLY)


class SummarizePreflightTests(unittest.TestCase):
    def test_summary_aggregates_categories_and_dirty_pages(self) -> None:
        classifications = [
            {"category": "publication-info", "needsVisualOcr": False},
            {"category": "text-only", "needsVisualOcr": False},
            {"category": "blank", "needsVisualOcr": False},
            {"category": "image-mixed", "needsVisualOcr": True},
            {"category": "text-only", "needsVisualOcr": False},
        ]
        summary = summarize_preflight(classifications)
        self.assertEqual(summary["totalPages"], 5)
        # 可处理 = 总数 - 空白 - 出版信息
        self.assertEqual(summary["processablePages"], 3)
        self.assertEqual(summary["suspectedDirtyCount"], 2)
        self.assertEqual(summary["blankPages"], [3])
        self.assertEqual(summary["publicationInfoPages"], [1])
        self.assertEqual(summary["imageMixedPages"], [4])
        self.assertEqual(summary["visualOcrNeededPages"], [4])

    def test_empty_document_yields_zeroed_summary(self) -> None:
        summary = summarize_preflight([])
        self.assertEqual(summary["totalPages"], 0)
        self.assertEqual(summary["processablePages"], 0)
        self.assertEqual(summary["suspectedDirtyCount"], 0)


if __name__ == "__main__":
    unittest.main()
