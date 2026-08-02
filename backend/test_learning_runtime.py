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


if __name__ == "__main__":
    unittest.main()
