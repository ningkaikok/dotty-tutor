from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory

from lesson_contracts import LessonDocument, lesson_document_from_payload
from storage import TutorStore


class LessonContractTests(unittest.TestCase):
    def test_adapts_existing_question_payload_to_programmable_blocks(self) -> None:
        payload = {
            "question": {
                "id": "linear-equation",
                "chapter": "方程",
                "knowledgePoint": "一元一次方程",
                "publicationStatus": "ready",
            },
            "lessonSteps": [{
                "id": "move-term",
                "title": "移项",
                "text": "把常数项移到右边。",
                "speechText": "先完成移项。",
                "action": "show-base",
            }],
        }
        document = lesson_document_from_payload(payload, source_upload_id="upload-1")
        validated = LessonDocument.model_validate(document)
        self.assertEqual(validated.lessonId, "linear-equation")
        self.assertEqual(validated.blocks[0].type, "diagram")
        self.assertEqual(validated.blocks[-1].type, "quiz")


class LearningStoreTests(unittest.TestCase):
    def test_persists_lesson_attempt_and_mastery(self) -> None:
        with TemporaryDirectory() as directory:
            store = TutorStore(
                database_url=f"sqlite+pysqlite:///{directory}/learning.sqlite3",
                data_root=directory,
            )
            document = {
                "lessonId": "lesson-1",
                "title": "一次方程",
                "version": 1,
                "status": "published",
                "sourceUploadId": None,
                "knowledgePoints": ["移项"],
                "blocks": [{
                    "id": "step-1",
                    "type": "diagram",
                    "title": "移项",
                    "payload": {"renderer": "geometry", "action": "show-base"},
                }],
            }
            store.save_lesson(document)
            restored = store.load_lesson("lesson-1")
            self.assertEqual(restored["blocks"][0]["type"], "diagram")

            store.create_learning_session(
                session_id="session-1",
                learner_id="student-1",
                lesson_id="lesson-1",
                started_at=1.0,
            )
            result = store.record_exercise_attempt(
                attempt_id="attempt-1",
                session_id="session-1",
                question_id="question-1",
                knowledge_point="移项",
                response={"text": "x=4"},
                assessment="correct",
                hint_level=0,
                duration_ms=800,
                created_at=2.0,
            )
            self.assertEqual(result["mastery"]["score"], 0.3)
            self.assertEqual(result["mastery"]["attemptCount"], 1)
            self.assertEqual(store.list_mastery("student-1")[0]["correctCount"], 1)

    def test_publishes_a_lesson_collection_and_deduplicates_sync_retries(self) -> None:
        with TemporaryDirectory() as directory:
            store = TutorStore(
                database_url=f"sqlite+pysqlite:///{directory}/learning.sqlite3",
                data_root=directory,
            )
            for lesson_id in ("lesson-a", "lesson-b"):
                store.save_lesson({
                    "lessonId": lesson_id,
                    "title": lesson_id,
                    "version": 1,
                    "status": "draft",
                    "sourceUploadId": "upload-1",
                    "knowledgePoints": ["分数"],
                    "blocks": [],
                    "questionPayload": {
                        "question": {"id": lesson_id, "knowledgePoint": "分数"},
                        "quality": {"status": "ready"},
                    },
                    "guideCards": [],
                })
            publication = store.create_publication(
                publication_id="paper-1",
                title="第一章互动试卷",
                source_upload_id="upload-1",
                lesson_ids=["lesson-a", "lesson-b"],
                status="draft",
                created_at=1.0,
            )
            self.assertEqual(publication["status"], "draft")
            store.update_publication_status("paper-1", "in_review")
            published = store.update_publication_status("paper-1", "published")
            self.assertEqual(published["status"], "published")
            self.assertTrue(all(item["status"] == "published" for item in published["lessons"]))

            store.create_learning_session(
                session_id="paper-session",
                learner_id="student-1",
                lesson_id="paper-1",
                started_at=1.0,
            )
            first = store.record_exercise_attempt(
                attempt_id="stable-attempt",
                session_id="paper-session",
                question_id="lesson-a",
                knowledge_point="分数",
                response={"text": "1/2"},
                assessment="correct",
                hint_level=0,
                duration_ms=100,
                created_at=2.0,
            )
            retry = store.record_exercise_attempt(
                attempt_id="stable-attempt",
                session_id="paper-session",
                question_id="lesson-a",
                knowledge_point="分数",
                response={"text": "1/2"},
                assessment="correct",
                hint_level=0,
                duration_ms=100,
                created_at=3.0,
            )
            self.assertEqual(first, retry)
            self.assertEqual(store.get_learning_session("paper-session")["attempts"], [{
                "attemptId": "stable-attempt",
                "questionId": "lesson-a",
                "knowledgePoint": "分数",
                "response": {"text": "1/2"},
                "assessment": "correct",
                "hintLevel": 0,
                "durationMs": 100,
                "createdAt": 2.0,
            }])

    def test_rejects_attempt_for_unknown_session(self) -> None:
        with TemporaryDirectory() as directory:
            store = TutorStore(
                database_url=f"sqlite+pysqlite:///{directory}/learning.sqlite3",
                data_root=directory,
            )
            with self.assertRaisesRegex(LookupError, "学习会话不存在"):
                store.record_exercise_attempt(
                    attempt_id="attempt-1",
                    session_id="missing",
                    question_id="question-1",
                    knowledge_point="移项",
                    response={},
                    assessment="incorrect",
                    hint_level=0,
                    duration_ms=0,
                    created_at=1.0,
                )

    def test_rejects_unsafe_publication_transitions_and_missing_quality(self) -> None:
        with TemporaryDirectory() as directory:
            store = TutorStore(
                database_url=f"sqlite+pysqlite:///{directory}/learning.sqlite3",
                data_root=directory,
            )
            store.save_lesson({
                "lessonId": "unreviewed",
                "title": "未审校题目",
                "version": 1,
                "status": "draft",
                "knowledgePoints": ["分数"],
                "blocks": [],
                "questionPayload": {"question": {"id": "unreviewed"}},
            })
            store.create_publication(
                publication_id="paper-unreviewed",
                title="待审校试卷",
                source_upload_id=None,
                lesson_ids=["unreviewed"],
                status="draft",
                created_at=1.0,
            )
            with self.assertRaisesRegex(ValueError, "不能从 draft 直接变为 published"):
                store.update_publication_status("paper-unreviewed", "published")
            store.update_publication_status("paper-unreviewed", "in_review")
            with self.assertRaisesRegex(ValueError, "未通过结构质量门禁"):
                store.update_publication_status("paper-unreviewed", "published")

    def test_attempt_id_cannot_cross_learning_sessions(self) -> None:
        with TemporaryDirectory() as directory:
            store = TutorStore(
                database_url=f"sqlite+pysqlite:///{directory}/learning.sqlite3",
                data_root=directory,
            )
            for session_id in ("session-a", "session-b"):
                store.create_learning_session(
                    session_id=session_id,
                    learner_id="student-1",
                    lesson_id="paper-1",
                    started_at=1.0,
                )
            store.record_exercise_attempt(
                attempt_id="shared-attempt",
                session_id="session-a",
                question_id="question-1",
                knowledge_point="分数",
                response={},
                assessment="correct",
                hint_level=0,
                duration_ms=100,
                created_at=2.0,
            )
            with self.assertRaisesRegex(LookupError, "不属于当前学习会话"):
                store.record_exercise_attempt(
                    attempt_id="shared-attempt",
                    session_id="session-b",
                    question_id="question-1",
                    knowledge_point="分数",
                    response={},
                    assessment="correct",
                    hint_level=0,
                    duration_ms=100,
                    created_at=3.0,
                )


if __name__ == "__main__":
    unittest.main()
