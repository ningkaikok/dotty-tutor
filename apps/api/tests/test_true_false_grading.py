"""判断题的确定性判定：并入判题器后，写入端复核对它同样生效。

此前判断题只在 ``tutor_engine`` 内联判定，``evaluate_structured_answer``
不覆盖它，因此写入端的服务端复核对这一类题会回退到客户端自报的判定。
"""

from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from typing import Any

from answer_evaluator import evaluate_structured_answer, normalize_true_false
from persistence.app_store import AppStore
from tests.postgres_test_support import PostgresTestCase

TRUE_FALSE_QUESTION = {
    "id": "question-tf",
    "questionType": "true-false",
    "knowledgePoint": "邻补角",
    "prompt": "两个邻补角一定相等。",
    "correctAnswer": "错误",
}


class NormalizeTrueFalseTests(unittest.TestCase):
    def test_negation_is_not_read_as_affirmation(self) -> None:
        """旧实现按肯定式优先匹配，把"不正确"判成了"正确"。"""
        self.assertEqual(normalize_true_false("不正确"), "错误")
        self.assertEqual(normalize_true_false("不对"), "错误")
        self.assertEqual(normalize_true_false("我觉得不正确"), "错误")

    def test_plain_forms(self) -> None:
        self.assertEqual(normalize_true_false("正确"), "正确")
        self.assertEqual(normalize_true_false("对"), "正确")
        self.assertEqual(normalize_true_false("true"), "正确")
        self.assertEqual(normalize_true_false("错误"), "错误")
        self.assertEqual(normalize_true_false("False"), "错误")

    def test_unrecognized_input_returns_none(self) -> None:
        """识别不出选择时返回 None，由模型兜底，绝不能默认判错。"""
        self.assertIsNone(normalize_true_false(""))
        self.assertIsNone(normalize_true_false("我不知道"))
        self.assertIsNone(normalize_true_false(None))


class EvaluateTrueFalseTests(unittest.TestCase):
    def test_correct_and_incorrect_are_graded(self) -> None:
        wrong = evaluate_structured_answer(TRUE_FALSE_QUESTION, "正确")
        assert wrong is not None
        self.assertEqual(wrong["assessment"], "incorrect")
        right = evaluate_structured_answer(TRUE_FALSE_QUESTION, "错误")
        assert right is not None
        self.assertEqual(right["assessment"], "correct")

    def test_evidence_shape_is_preserved(self) -> None:
        """证据契约沿用内联实现的形状，报告和前端不需要跟着改。"""
        result = evaluate_structured_answer(TRUE_FALSE_QUESTION, "错误")
        assert result is not None
        evidence = result["evaluationEvidence"]
        self.assertEqual(evidence["strategy"], "true-false-match")
        self.assertEqual(evidence["submittedLabel"], "错误")
        self.assertIn("evaluatorVersion", evidence)

    def test_reply_does_not_restate_the_answer(self) -> None:
        """判断题只有两个选项，说"判断不对"已经等于给出答案，不再复述标准答案。"""
        result = evaluate_structured_answer(TRUE_FALSE_QUESTION, "正确")
        assert result is not None
        self.assertNotIn("错误", result["reply"])

    def test_question_specific_guidance_is_kept(self) -> None:
        """并入通用判题器不能把贴题引导退化成通用文案——它会流进引导卡。"""
        result = evaluate_structured_answer(TRUE_FALSE_QUESTION, "错误")
        assert result is not None
        self.assertEqual(result["knowledge"], ["邻补角"])
        self.assertEqual(result["stuckAt"], "需要根据题干条件判断命题真伪。")

    def test_unreadable_answer_falls_back_to_the_model(self) -> None:
        self.assertIsNone(evaluate_structured_answer(TRUE_FALSE_QUESTION, "我不知道"))
        self.assertIsNone(evaluate_structured_answer({**TRUE_FALSE_QUESTION, "correctAnswer": ""}, "正确"))


class TrueFalseWritePathTests(PostgresTestCase):
    def _store_with_question(self, directory: str) -> AppStore:
        store = AppStore(database_url=self.database_url, data_root=directory)
        store.save_lesson({
            "lessonId": "lesson-tf",
            "title": "判断题",
            "version": 1,
            "status": "published",
            "sourceUploadId": None,
            "knowledgePoints": ["邻补角"],
            "blocks": [],
            "questionPayload": {
                "question": TRUE_FALSE_QUESTION,
                "quality": {"status": "ready"},
            },
        })
        store.create_publication(
            publication_id="paper-tf", title="判断题卷", source_upload_id=None,
            lesson_ids=["lesson-tf"], status="draft", created_at=1.0,
        )
        store.update_publication_status("paper-tf", "in_review")
        store.update_publication_status("paper-tf", "published")
        store.create_learning_session(
            session_id="session-tf", learner_id="student-tf",
            publication_id="paper-tf", started_at=1.0,
        )
        return store

    def _stored_assessment(self, store: AppStore, attempt_id: str) -> Any:
        from sqlalchemy import select

        from persistence.schema import exercise_attempts

        with store.engine.connect() as connection:
            row = connection.execute(
                select(exercise_attempts.c.assessment).where(
                    exercise_attempts.c.attempt_id == attempt_id
                )
            ).first()
        assert row is not None
        return row[0]

    def test_fabricated_true_false_verdict_is_now_corrected(self) -> None:
        """收口验证：判断题此前会回退到客户端自报的判定，现在由服务端复核。"""
        with TemporaryDirectory() as directory:
            store = self._store_with_question(directory)
            result = store.record_exercise_attempt(
                attempt_id="attempt-tf",
                session_id="session-tf",
                question_id="question-tf",
                response={"text": "正确"},
                assessment="correct",
                hint_level=0,
                duration_ms=100,
                created_at=2.0,
            )
            self.assertEqual(self._stored_assessment(store, "attempt-tf"), "incorrect")
            self.assertEqual(result["mastery"]["correctCount"], 0)

    def test_honest_true_false_answer_is_unchanged(self) -> None:
        with TemporaryDirectory() as directory:
            store = self._store_with_question(directory)
            store.record_exercise_attempt(
                attempt_id="attempt-tf-ok",
                session_id="session-tf",
                question_id="question-tf",
                response={"text": "错误"},
                assessment="correct",
                hint_level=0,
                duration_ms=100,
                created_at=2.0,
            )
            self.assertEqual(self._stored_assessment(store, "attempt-tf-ok"), "correct")


if __name__ == "__main__":
    unittest.main()
