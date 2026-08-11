"""Unit tests for the OCR source quality gate."""

from __future__ import annotations

import unittest

from ocr_quality import MAX_OCR_RETRIES, evaluate_page_quality, evaluate_question_quality


class OcrQualityTests(unittest.TestCase):
    def test_accepts_chinese_question_marker_and_inline_choices(self) -> None:
        decision = evaluate_question_quality(
            "第 12 题 下列正确的是（ ）A. 1 B. 2 C. 3 D. 4",
            page_number=2,
            question_number="12",
            provider="mineru",
        )
        self.assertEqual(decision["status"], "ready")

    def test_corrupted_formula_requests_provider_retry(self) -> None:
        decision = evaluate_page_quality(
            r"温度上升 7\textbackslash\textcirc C。",
            page_number=1,
            provider="pypdf",
        )
        self.assertEqual(decision["status"], "retry")
        self.assertIn("formula_command_corrupted", decision["problems"])

    def test_accepts_complete_question_with_formula_and_image_reference(self) -> None:
        text = (
            "12. 计算 $x + 1 = 3$。\n"
            "(A) 1\n(B) 2\n(C) 3\n(D) 4\n"
            "![图](images/question-12.png)"
        )
        result = evaluate_question_quality(
            text,
            page_number=3,
            question_number=12,
            provider="mineru",
            expected_images=["images/question-12.png"],
        )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["problems"], [])
        self.assertIsNone(result["retryScope"])

    def test_requests_question_only_retry_and_upgrades_pypdf_to_mineru(self) -> None:
        result = evaluate_question_quality(
            "12. 请选择正确答案。\n(A) 1\n(B) 2\n(C) 3",
            page_number=3,
            question_number=12,
            provider="pypdf",
        )

        self.assertEqual(result["status"], "retry")
        self.assertIn("choice_options_incomplete", result["problems"])
        self.assertEqual(result["recommendedProvider"], "mineru")
        self.assertEqual(result["retryScope"], {
            "type": "question", "pageNumbers": [3], "questionNumbers": ["12"],
        })

    def test_quarantines_unrecoverable_manual_page(self) -> None:
        result = evaluate_page_quality("\ufffd\ufffd\ufffd", page_number=7, provider="manual")

        self.assertEqual(result["status"], "quarantine")
        self.assertIn("garbled_text_rate_high", result["problems"])
        self.assertIsNone(result["recommendedProvider"])

    def test_quarantines_after_retry_limit_without_provider_downgrade(self) -> None:
        result = evaluate_page_quality(
            "9. 设 $x = 1。",
            page_number=9,
            provider="mineru",
            retry_count=MAX_OCR_RETRIES,
        )

        self.assertEqual(result["status"], "quarantine")
        self.assertIn("inline_formula_delimiter_unbalanced", result["problems"])
        self.assertIsNone(result["recommendedProvider"])
        self.assertIsNone(result["retryScope"])


if __name__ == "__main__":
    unittest.main()
