"""学生投影：面向学生的题目不得携带任何标准答案。

此前 ``_public_lesson`` 只按名字剥掉几个诊断字段，答案键随题目一起下发；
变式题列表更是原样返回整个 ``questionPayload``。这些用例同时守住两件事：
答案键一个都不能出现，渲染必需的字段一个都不能少。
"""

from __future__ import annotations

import json
import unittest

from domain.questions.student_view import student_question, student_question_payload
from routers.publication_routes import _public_lesson

# 覆盖每一种带答案的结构：顶层、填空、数值、画线、多小问。
FULL_QUESTION = {
    "id": "q-1",
    "questionType": "fill-blank",
    "selectionMode": "single",
    "chapter": "方程",
    "knowledgePoint": "一元二次方程",
    "questionNumber": "7",
    "prompt": "解方程 $x^2=4$",
    "givens": ["x 为实数"],
    "options": ["A", "B"],
    "contentBlocks": [{"id": "stem-1", "type": "text", "text": "解方程"}],
    "imageUrls": ["/api/uploads/u1/assets/b1/a.jpg"],
    "correctAnswer": "2",
    "correctAnswers": ["2", "-2"],
    "blanks": [{
        "id": "b1", "label": "空1", "answerType": "numeric",
        "unit": "cm", "correctAnswers": ["2"], "tolerance": 0.01,
    }],
    "answerSpec": {
        "answerType": "numeric", "unit": "cm",
        "expected": "2", "accepted": ["2.0"], "tolerance": 0.01,
    },
    "interaction": {
        "type": "draw-line",
        "instruction": "连接对应点",
        "points": [{"id": "A", "label": "A", "x": 0.1, "y": 0.2}],
        "requiredConnections": [["A", "B"]],
    },
    "subQuestions": [{
        "id": "s1", "label": "(1)", "prompt": "求正根", "questionType": "numeric",
        "evaluation": {"mode": "deterministic"},
        "options": [],
        "correctAnswer": "2",
        "correctAnswers": ["2"],
        "blanks": [{"id": "sb1", "label": "空", "answerType": "numeric", "correctAnswers": ["2"]}],
        "answerSpec": {"answerType": "numeric", "expected": "2", "accepted": ["2.0"], "unit": ""},
        "interaction": {"type": "none", "instruction": "", "points": [], "requiredConnections": []},
    }],
}

# 答案键的完整清单：任何一个出现在学生投影里都是泄漏。
ANSWER_KEYS = ("correctAnswer", "correctAnswers", "expected", "accepted", "requiredConnections")


class StudentQuestionProjectionTests(unittest.TestCase):
    def test_no_answer_key_survives_anywhere_in_the_projection(self) -> None:
        serialized = json.dumps(student_question(FULL_QUESTION), ensure_ascii=False)
        for key in ANSWER_KEYS:
            self.assertNotIn(f'"{key}"', serialized, f"{key} 泄漏到学生投影")
        # 答案值本身也不能以任何形式残留。
        self.assertNotIn('"2.0"', serialized)

    def test_rendering_fields_are_preserved(self) -> None:
        """剥答案不能顺手剥掉渲染必需的字段，否则学生端会白屏或缺单位。"""
        projected = student_question(FULL_QUESTION)
        for key in (
            "id", "questionType", "selectionMode", "chapter", "knowledgePoint",
            "questionNumber", "prompt", "givens", "options", "contentBlocks", "imageUrls",
        ):
            self.assertIn(key, projected, f"渲染必需字段 {key} 被误删")
        self.assertEqual(projected["blanks"][0]["unit"], "cm")
        self.assertEqual(projected["blanks"][0]["answerType"], "numeric")
        self.assertEqual(projected["answerSpec"]["unit"], "cm")
        # 画线题要端点和说明，但不要标准连线。
        self.assertEqual(projected["interaction"]["points"][0]["id"], "A")
        self.assertEqual(projected["interaction"]["instruction"], "连接对应点")
        sub = projected["subQuestions"][0]
        self.assertEqual(sub["label"], "(1)")
        self.assertEqual(sub["evaluation"]["mode"], "deterministic")

    def test_unknown_new_field_is_dropped_by_default(self) -> None:
        """白名单的意义：以后新增的字段默认不下发，而不是默认泄漏。"""
        projected = student_question({**FULL_QUESTION, "solutionOutline": "先开方"})
        self.assertNotIn("solutionOutline", projected)

    def test_payload_projection_keeps_sibling_keys(self) -> None:
        payload = {"question": FULL_QUESTION, "lessonSteps": [{"id": "s"}], "architecture": {}}
        projected = student_question_payload(payload)
        self.assertEqual(projected["lessonSteps"], [{"id": "s"}])
        self.assertNotIn("correctAnswer", projected["question"])

    def test_non_dict_input_is_safe(self) -> None:
        self.assertEqual(student_question(None), {})
        self.assertEqual(student_question("x"), {})
        self.assertEqual(student_question_payload(None), {})


class PublicLessonTests(unittest.TestCase):
    def test_published_lesson_no_longer_ships_the_answer_key(self) -> None:
        public = _public_lesson({
            "lessonId": "lesson-1",
            "title": "一次方程",
            "version": 1,
            "status": "published",
            "knowledgePoints": ["移项"],
            "blocks": [],
            "questionPayload": {
                "question": {
                    **FULL_QUESTION,
                    "publicationStatus": "ready",
                    "sourceArtifactUrl": "/private/source.md",
                    "promptArtifactUrl": "/private/prompt.md",
                },
                "review": {"status": "reviewed"},
                "quality": {"status": "ready"},
                "modelRun": {"provider": "codex", "model": "secret"},
            },
            "guideCards": [{"hint": "内部提示"}],
        })
        serialized = json.dumps(public, ensure_ascii=False)
        for key in ANSWER_KEYS:
            self.assertNotIn(f'"{key}"', serialized, f"{key} 泄漏到已发布试卷")
        # 原有的诊断脱敏不能因为换成白名单而回退。
        payload = public["questionPayload"]
        self.assertNotIn("review", payload)
        self.assertNotIn("quality", payload)
        self.assertNotIn("publicationStatus", payload["question"])
        self.assertNotIn("sourceArtifactUrl", payload["question"])
        self.assertEqual(payload["modelRun"]["provider"], "published")
        self.assertEqual(public["guideCards"], [])
        # 题目仍然可渲染。
        self.assertEqual(payload["question"]["prompt"], "解方程 $x^2=4$")


if __name__ == "__main__":
    unittest.main()
