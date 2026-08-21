"""Regression tests for unstructured content residue in question stems.

This bug has recurred multiple times (see commits 74118ed, c363620, 5f52ed8,
947ed01): a cleanup rule gets written inside one special-case branch, a
different question shape slips past it, and the raw markdown/image markup
ends up rendered as literal text to the student. These tests pin down the
two structural fixes: the cleanup now runs on every question, and a
deterministic validator check catches any future regression instead of
relying on someone spotting it in the UI.
"""

from __future__ import annotations

import unittest

from domain.questions.pipeline import apply_question_quality_gate, validate_question_payload


class QuestionContentResidueTests(unittest.TestCase):
    def test_strips_image_reference_from_ordinary_question_prompt(self) -> None:
        """普通题（非 A-D 图片选择题）模型误把图片路径写回 prompt 时，仍需被清理。

        清理逻辑此前只写在 A-D 图片选择题分支里，其它题型（例如这里的统计表
        + 单张图题）会原样保留 `![](images/xxx.jpg)`，一路流进 contentBlocks
        并展示给学生。
        """
        leaked_reference = "![](images/49ded691c9294df2b581302eca963010653822afc68bf9d444aaba5999b3cbb1.jpg)"
        payload = {
            "question": {
                "questionNumber": "5",
                "questionType": "short-answer",
                "prompt": f"某班统计阅读量如下表所示，求平均数。{leaked_reference}",
                "options": [],
                "givens": [],
                "imageUrls": [],
            },
        }
        quality = apply_question_quality_gate(payload, "5. 某班统计阅读量如下表所示，求平均数。", [])

        prompt = payload["question"]["prompt"]
        self.assertNotIn("![", prompt)
        self.assertNotIn("images/", prompt)

        text_blocks = [block for block in payload["question"]["contentBlocks"] if block.get("type") == "text"]
        self.assertTrue(text_blocks, "cleaned prompt should still produce a text content block")
        for block in text_blocks:
            self.assertNotIn("images/", block["text"])

        self.assertFalse(
            any("残留未结构化的图片引用" in error for error in quality["errors"]),
            quality["errors"],
        )

    def test_residue_check_flags_leftover_image_reference_in_text_block(self) -> None:
        """安全网本身要能生效：即便清理逻辑被绕过，残留也必须让题目进入 needs_review。"""
        payload = {
            "question": {
                "questionNumber": "6",
                "prompt": "某班统计如下表，求平均数。",
                "options": [],
                "imageUrls": [],
                "contentBlocks": [
                    {"id": "stem-1", "type": "text", "text": "某班统计如下表，求平均数。![](images/leaked.jpg)"},
                ],
            },
        }
        quality = validate_question_payload(payload, "6. 某班统计如下表，求平均数。", [])

        self.assertEqual(quality["status"], "needs_review")
        self.assertTrue(
            any("题干残留未结构化的图片引用" in error for error in quality["errors"]),
            quality["errors"],
        )


if __name__ == "__main__":
    unittest.main()
