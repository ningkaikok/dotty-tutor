"""Unit tests for the OCR source quality gate."""

from __future__ import annotations

import unittest

from application.services.courseware import CoursewareService
from ocr_quality import (
    MAX_OCR_RETRIES,
    evaluate_page_quality,
    evaluate_question_quality,
)


class OcrQualityTests(unittest.TestCase):
    def test_user_flags_semantic_ocr_error_then_courseware_cannot_use_the_source(self) -> None:
        # Given a syntactically valid synthetic sign error explicitly flagged by a reviewer
        source = {"pages": [{"page": 214, "text": "$y+37=13$", "flags": ["formula_mismatch"]}]}
        # When courseware selects that page for planning, review or publication
        with self.assertRaisesRegex(ValueError, "formula_mismatch"):
            CoursewareService._selected_pages(source, 214, 214)
        # Then only a corrected revision with the review flag resolved is usable
        corrected = {"pages": [{"page": 214, "text": "$y+37=-13$", "flags": []}]}
        self.assertEqual(CoursewareService._selected_pages(corrected, 214, 214), corrected["pages"])

    def test_user_uses_old_source_without_cached_issues_then_courseware_rechecks_formula_integrity(self) -> None:
        # Given an old source revision lacking any previously computed issue or flag
        source = {"pages": [{"page": 1, "text": r"$x=\frac{1}{2$", "flags": []}]}
        # When it is selected by the publication workflow
        with self.assertRaisesRegex(ValueError, "formula_group_unbalanced"):
            CoursewareService._selected_pages(source, 1, 1)

    def test_user_receives_truncated_formula_then_ocr_is_quarantined_without_guessing(self) -> None:
        # Given synthetic OCR with paired dollars but a truncated fraction group
        damaged = r"Solve $x = \frac{12}{3$ and check the original page."
        # When the highest-fidelity provider has exhausted its retry budget
        decision = evaluate_page_quality(
            damaged, page_number=214, provider="mineru", retry_count=MAX_OCR_RETRIES,
        )
        # Then the source cannot be marked ready or silently repaired
        self.assertEqual(decision["status"], "quarantine")
        self.assertIn("formula_group_unbalanced", decision["problems"])

    def test_formula_group_check_accepts_nested_groups_and_escaped_set_braces(self) -> None:
        for formula in (r"$\frac{1}{\sqrt{4}}$", r"$\{x \mid x>0\}$", r"Budget \$5; $x=1$", "Text {without math}"):
            with self.subTest(formula=formula):
                self.assertEqual(evaluate_page_quality(formula, page_number=1, provider="mineru")["status"], "ready")

    def test_backslash_formula_delimiters_cannot_hide_truncated_groups(self) -> None:
        for formula in (r"\[\frac{1}{2\]", r"\(x^{2\)", r"\[x+1", r"\(x+1", "$}x{$"):
            with self.subTest(formula=formula):
                self.assertNotEqual(evaluate_page_quality(formula, page_number=1, provider="mineru")["status"], "ready")

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
