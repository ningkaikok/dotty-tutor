"""写入端判定复核：掌握度不接受客户端自报的判定结果。

掌握度是老师看板、喂回出题和个性化作业的唯一输入。诚实客户端的判定来自
``/api/help``，但两次调用之间没有任何绑定，所以写入端必须自己重判一次。
这些用例覆盖三种情形：伪造被纠正、诚实作答不受影响、没有答案规格的题保留
客户端值（这类题本来就由模型判定）。
"""

from __future__ import annotations

from tempfile import TemporaryDirectory
from typing import Any

from persistence.app_store import AppStore
from tests.postgres_test_support import PostgresTestCase

CHOICE_QUESTION = {
    "id": "question-choice",
    "knowledgePoint": "一元一次方程",
    "questionType": "choice",
    "prompt": "解方程 2x + 1 = 5，x 等于？",
    "options": ["1", "2", "3", "4"],
    "correctAnswers": ["B"],
}

OPEN_QUESTION = {
    "id": "question-open",
    "knowledgePoint": "证明题",
    "questionType": "short-answer",
    "prompt": "说明为什么两个邻补角相等时该角为直角。",
}


def _lesson(lesson_id: str, question: dict[str, Any]) -> dict[str, Any]:
    return {
        "lessonId": lesson_id,
        "title": "验证用试卷",
        "version": 1,
        "status": "published",
        "sourceUploadId": None,
        "knowledgePoints": [question["knowledgePoint"]],
        "blocks": [],
        "questionPayload": {"question": question, "quality": {"status": "ready"}},
    }


class AttemptAssessmentVerificationTests(PostgresTestCase):
    def _published_store(self, directory: str, questions: list[dict[str, Any]]) -> AppStore:
        store = AppStore(database_url=self.database_url, data_root=directory)
        lesson_ids = []
        for index, question in enumerate(questions):
            lesson_id = f"lesson-{index}"
            lesson_ids.append(lesson_id)
            store.save_lesson(_lesson(lesson_id, question))
        store.create_publication(
            publication_id="paper-1",
            title="验证用试卷",
            source_upload_id=None,
            lesson_ids=lesson_ids,
            status="draft",
            created_at=1.0,
        )
        store.update_publication_status("paper-1", "in_review")
        store.update_publication_status("paper-1", "published")
        store.create_learning_session(
            session_id="session-1",
            learner_id="student-1",
            publication_id="paper-1",
            started_at=1.0,
        )
        return store

    def _stored_assessment(self, store: AppStore, attempt_id: str) -> str:
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

    def test_fabricated_correct_answer_is_overridden_by_the_server(self) -> None:
        """客户端可以跳过 /api/help 直接自报 correct；写入端必须自己重判。"""
        with TemporaryDirectory() as directory:
            store = self._published_store(directory, [CHOICE_QUESTION])
            result = store.record_exercise_attempt(
                attempt_id="attempt-fabricated",
                session_id="session-1",
                question_id="question-choice",
                response={"text": "D", "interactionResult": {"selectedOptions": ["D"]}},
                assessment="correct",
                hint_level=0,
                duration_ms=100,
                created_at=2.0,
            )
            self.assertEqual(self._stored_assessment(store, "attempt-fabricated"), "incorrect")
            # 掌握度必须跟着服务端判定走，而不是客户端声称的结果。
            self.assertEqual(result["mastery"]["correctCount"], 0)

    def test_honest_correct_answer_is_unchanged(self) -> None:
        """诚实作答不受影响：服务端重判与 /api/help 的判定同源，结果一致。"""
        with TemporaryDirectory() as directory:
            store = self._published_store(directory, [CHOICE_QUESTION])
            result = store.record_exercise_attempt(
                attempt_id="attempt-honest",
                session_id="session-1",
                question_id="question-choice",
                response={"text": "B", "interactionResult": {"selectedOptions": ["B"]}},
                assessment="correct",
                hint_level=0,
                duration_ms=100,
                created_at=2.0,
            )
            self.assertEqual(self._stored_assessment(store, "attempt-honest"), "correct")
            self.assertEqual(result["mastery"]["correctCount"], 1)

    def test_fabricated_incorrect_is_also_corrected(self) -> None:
        """复核是双向的：答对了却上报 incorrect 同样以服务端为准。"""
        with TemporaryDirectory() as directory:
            store = self._published_store(directory, [CHOICE_QUESTION])
            store.record_exercise_attempt(
                attempt_id="attempt-understated",
                session_id="session-1",
                question_id="question-choice",
                response={"text": "B", "interactionResult": {"selectedOptions": ["B"]}},
                assessment="incorrect",
                hint_level=0,
                duration_ms=100,
                created_at=2.0,
            )
            self.assertEqual(self._stored_assessment(store, "attempt-understated"), "correct")

    def test_question_without_answer_spec_keeps_the_client_assessment(self) -> None:
        """开放题没有可确定判定的答案规格，保留模型判定结果，不能当成答错。"""
        with TemporaryDirectory() as directory:
            store = self._published_store(directory, [OPEN_QUESTION])
            store.record_exercise_attempt(
                attempt_id="attempt-open",
                session_id="session-1",
                question_id="question-open",
                response={"text": "因为两个邻补角相加是平角。"},
                assessment="correct",
                hint_level=0,
                duration_ms=100,
                created_at=2.0,
            )
            self.assertEqual(self._stored_assessment(store, "attempt-open"), "correct")

    def test_offline_sync_batch_is_verified_per_attempt(self) -> None:
        """离线补传批次同样逐条复核，不能因为走 /sync 就绕开判定权。"""
        with TemporaryDirectory() as directory:
            store = self._published_store(directory, [CHOICE_QUESTION, OPEN_QUESTION])
            store.record_exercise_attempt(
                attempt_id="attempt-sync-1",
                session_id="session-1",
                question_id="question-choice",
                response={"text": "A", "interactionResult": {"selectedOptions": ["A"]}},
                assessment="correct",
                hint_level=0,
                duration_ms=100,
                created_at=2.0,
            )
            store.record_exercise_attempt(
                attempt_id="attempt-sync-2",
                session_id="session-1",
                question_id="question-open",
                response={"text": "邻补角相加为平角。"},
                assessment="correct",
                hint_level=0,
                duration_ms=100,
                created_at=3.0,
            )
            self.assertEqual(self._stored_assessment(store, "attempt-sync-1"), "incorrect")
            self.assertEqual(self._stored_assessment(store, "attempt-sync-2"), "correct")

    def test_idempotent_retry_returns_the_first_verified_result(self) -> None:
        """重放同一个 attemptId 不得二次判定，也不得覆盖首次写入的判定。"""
        with TemporaryDirectory() as directory:
            store = self._published_store(directory, [CHOICE_QUESTION])
            store.record_exercise_attempt(
                attempt_id="attempt-retry",
                session_id="session-1",
                question_id="question-choice",
                response={"text": "D", "interactionResult": {"selectedOptions": ["D"]}},
                assessment="correct",
                hint_level=0,
                duration_ms=100,
                created_at=2.0,
            )
            store.record_exercise_attempt(
                attempt_id="attempt-retry",
                session_id="session-1",
                question_id="question-choice",
                response={"text": "B", "interactionResult": {"selectedOptions": ["B"]}},
                assessment="correct",
                hint_level=0,
                duration_ms=100,
                created_at=3.0,
            )
            self.assertEqual(self._stored_assessment(store, "attempt-retry"), "incorrect")


if __name__ == "__main__":
    import unittest

    unittest.main()
