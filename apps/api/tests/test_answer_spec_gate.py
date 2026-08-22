"""Regression tests for answer-spec data that no grading path can ever read.

多小问题目（本地题库 64 道里有 11 道，约 17%）当前没有结构化表示：questionType、
correctAnswer 和 answerSpec 都假设一道题只有一个答案。真实坏样本是一道证明题被归
成 short-answer，却带着只覆盖第 (2) 问的 numeric answerSpec。short-answer 不进入
确定性判题分支，这条错配今天读不到、也就不会被任何测试或页面暴露——正是这类沉默
数据在题型被改动时会突然变成误判。
"""

from __future__ import annotations

import unittest

from domain.questions.pipeline import validate_question_payload


def _payload(question_type: str, answer_spec: dict | None, prompt: str) -> dict:
    return {
        "question": {
            "questionNumber": "26",
            "questionType": question_type,
            "prompt": prompt,
            "options": [],
            "imageUrls": [],
            "answerSpec": answer_spec,
            "contentBlocks": [{"id": "stem-1", "type": "text", "text": prompt}],
        },
    }


# 取自「2018南京中考数学试卷」第 26 题：(1) 是证明，(2) 才是那个 5/2。
PROOF_PROMPT = (
    "26.（8分）如图，在正方形ABCD中，E是AB上一点，连接DE。\n\n"
    "（1）求证：△AFG∽△DFC；\n\n"
    "（2）若正方形ABCD的边长为4，AE=1，求⊙O的半径。"
)
NUMERIC_SPEC = {"answerType": "numeric", "expected": "5/2", "accepted": ["5/2", "2.5"], "tolerance": 0.0, "unit": ""}


class AnswerSpecGateTests(unittest.TestCase):
    def test_short_answer_carrying_an_answer_spec_is_flagged(self) -> None:
        """short-answer 不参与确定性判题，携带 answerSpec 一定是错配数据。"""
        quality = validate_question_payload(_payload("short-answer", NUMERIC_SPEC, PROOF_PROMPT), PROOF_PROMPT, [])

        self.assertEqual(quality["status"], "needs_review")
        self.assertTrue(
            any("不参与确定性判题，却携带 answerSpec" in error for error in quality["errors"]),
            quality["errors"],
        )

    def test_numeric_question_keeps_its_answer_spec(self) -> None:
        """可判题的题型必须继续允许 answerSpec，不能误伤。"""
        prompt = "1. 求圆的半径。"
        quality = validate_question_payload(_payload("numeric", NUMERIC_SPEC, prompt), prompt, [])

        self.assertFalse(
            any("answerSpec" in error for error in quality["errors"]),
            quality["errors"],
        )

    def test_multi_part_question_is_warned_not_blocked(self) -> None:
        """多小问只提示人工确认，不阻断发布：结构化支持落地前它仍是合法内容。"""
        quality = validate_question_payload(_payload("short-answer", None, PROOF_PROMPT), PROOF_PROMPT, [])

        self.assertEqual(quality["status"], "ready", quality["errors"])
        self.assertTrue(
            any("多个小问" in warning for warning in quality["warnings"]),
            quality["warnings"],
        )


if __name__ == "__main__":
    unittest.main()
