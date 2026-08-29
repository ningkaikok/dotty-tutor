"""导入前题源质量报告的确定性回归测试。"""

from __future__ import annotations

import unittest

from domain.questions.quality import build_import_quality_report


class QuestionQualityReportTests(unittest.TestCase):
    def test_cross_batch_overlap_is_not_reported_as_duplicate(self) -> None:
        def blocks(number: str, image: str) -> list[tuple[str, str, list[str]]]:
            return [(number, f"{number}、题目", [image])]
        report = build_import_quality_report(
            [
                {"id": "batch-001", "startPage": 1, "endPage": 5, "source": "", "blocks": blocks("1", "a.png")},
                {"id": "batch-002", "startPage": 6, "endPage": 10, "source": "", "blocks": blocks("1", "a.png")},
            ],
            total_pages=10,
        )
        self.assertEqual(report["duplicateQuestionNumbers"], [])
        self.assertEqual(report["imageAttributionConflicts"], [])
        self.assertEqual(report["expectedQuestionCount"], 1)

    def test_duplicate_and_image_conflicts_block_generation(self) -> None:
        report = build_import_quality_report(
            [{
                "id": "batch-001",
                "startPage": 1,
                "endPage": 1,
                "source": "<!-- page 1 -->",
                "blocks": [
                    ("1", "1、第一题", ["shared.png"]),
                    ("1", "1、重复题", ["shared.png"]),
                    ("2", "2、第二题", ["shared.png"]),
                ],
            }],
            total_pages=1,
        )
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["readyForFullPaper"])
        self.assertEqual(report["duplicateQuestionNumbers"], ["1"])
        self.assertEqual(report["imageAttributionConflicts"][0]["questionNumbers"], ["1", "2"])

    def test_long_source_with_too_few_questions_is_blocked(self) -> None:
        report = build_import_quality_report(
            [{
                "id": "batch-001",
                "startPage": 1,
                "endPage": 5,
                "source": "OCR text。" * 300,
                "blocks": [("2", "2、说明被误识别成题目", [])],
            }],
            total_pages=5,
        )
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(any("题号过少" in item for item in report["blockers"]))


if __name__ == "__main__":
    unittest.main()
