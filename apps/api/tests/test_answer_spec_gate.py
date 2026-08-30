"""Regression tests for answer-spec data that no grading path can ever read.

多小问题目必须拥有独立的小问答案边界。真实坏样本是一道证明题被归成 short-answer，
却带着只覆盖第 (2) 问的 numeric answerSpec；结构化契约落地后，父题答案和 tutor-only
小问的自动答案都必须被质量门禁拒绝。
"""

from __future__ import annotations

import unittest

from answer_evaluator import evaluate_structured_answer
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

    def test_multi_part_question_without_structure_is_quarantined(self) -> None:
        """检测到多个小问但没有结构化契约时必须隔离，避免答案边界丢失。"""
        quality = validate_question_payload(_payload("short-answer", None, PROOF_PROMPT), PROOF_PROMPT, [])

        self.assertEqual(quality["status"], "needs_review", quality["errors"])
        self.assertTrue(any("缺少 subQuestions" in error for error in quality["errors"]), quality["errors"])

    def test_structured_sub_questions_reject_parent_answer_and_tutor_answer(self) -> None:
        question = _payload("short-answer", None, "26.（1）证明；（2）求半径。")["question"]
        question["correctAnswer"] = "错误的父题答案"
        question["subQuestions"] = [
            {
                "id": "sq-1", "label": "（1）", "prompt": "证明。", "questionType": "short-answer",
                "evaluation": {"mode": "tutor", "reason": "证明题需老师反馈"},
                "correctAnswer": "不应携带", "contentBlocks": [],
            },
            {
                "id": "sq-2", "label": "（2）", "prompt": "求半径。", "questionType": "numeric",
                "evaluation": {"mode": "deterministic", "reason": None},
                "answerSpec": NUMERIC_SPEC, "contentBlocks": [],
            },
        ]
        quality = validate_question_payload({"question": question}, question["prompt"], [])
        self.assertEqual(quality["status"], "needs_review")
        self.assertTrue(any("父题答案" in error for error in quality["errors"]))
        self.assertTrue(any("tutor-only" in error for error in quality["errors"]))

    def test_structured_sub_questions_allow_mixed_tutor_and_deterministic_parts(self) -> None:
        prompt = "26.（1）说明理由。\n（2）求半径。"
        question = _payload("short-answer", None, prompt)["question"]
        question["subQuestions"] = [
            {
                "id": "sq-1", "label": "（1）", "prompt": "说明理由。", "questionType": "short-answer",
                "evaluation": {"mode": "tutor", "reason": "需要解释"}, "contentBlocks": [],
            },
            {
                "id": "sq-2", "label": "（2）", "prompt": "求半径。", "questionType": "numeric",
                "evaluation": {"mode": "deterministic", "reason": None},
                "answerSpec": NUMERIC_SPEC, "contentBlocks": [],
            },
        ]
        quality = validate_question_payload({"question": question}, prompt, [])
        self.assertEqual(quality["status"], "ready", quality["errors"])

    def test_default_parent_interaction_does_not_block_structured_parts(self) -> None:
        prompt = "26.（1）说明理由。\n（2）求半径。"
        question = _payload("short-answer", None, prompt)["question"]
        question["interaction"] = {
            "type": "none", "instruction": "", "points": [], "requiredConnections": [],
        }
        question["subQuestions"] = [{
            "id": "sq-1", "label": "（1）", "prompt": "说明理由。", "questionType": "short-answer",
            "evaluation": {"mode": "tutor", "reason": "需要解释"}, "contentBlocks": [],
        }]
        quality = validate_question_payload({"question": question}, prompt, [])
        self.assertEqual(quality["status"], "ready", quality["errors"])

    def test_evaluator_reports_each_sub_question_and_withholds_mastery_for_tutor_part(self) -> None:
        question = {
            "questionType": "short-answer",
            "subQuestions": [
                {
                    "id": "sq-1", "questionType": "short-answer", "evaluation": {"mode": "tutor"},
                },
                {
                    "id": "sq-2", "questionType": "numeric", "evaluation": {"mode": "deterministic"},
                    "answerSpec": NUMERIC_SPEC,
                },
            ],
        }
        result = evaluate_structured_answer(question, "", {
            "subQuestionAnswers": {
                "sq-1": {"text": "因为两边相等"},
                "sq-2": {"numericAnswer": "2.5"},
            },
        })
        self.assertIsNotNone(result)
        self.assertEqual(result["assessment"], "partial")
        self.assertFalse(result["evaluationSummary"]["masteryEligible"])
        self.assertEqual(
            [part["status"] for part in result["evaluationSummary"]["parts"]],
            ["tutor", "correct"],
        )


if __name__ == "__main__":
    unittest.main()
