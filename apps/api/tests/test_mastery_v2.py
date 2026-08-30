from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from domain.learning.mastery import (
    derive_mastery,
    knowledge_point_id,
    normalize_knowledge_point_name,
)
from persistence.app_store import AppStore
from routers.learning_routes import build_learning_router


class MasteryAlgorithmTests(unittest.TestCase):
    def test_latest_attempt_per_question_and_evidence_cap(self) -> None:
        evidence = [
            {"publication_id": "paper", "question_id": "q1", "attempt_id": "old", "assessment": "incorrect", "created_at": 1},
            {"publication_id": "paper", "question_id": "q1", "attempt_id": "new", "assessment": "correct", "created_at": 2},
            {"publication_id": "paper", "question_id": "q2", "attempt_id": "q2", "assessment": "partial", "created_at": 3},
        ]
        result = derive_mastery(evidence)
        self.assertEqual(result["evidence_count"], 2)
        self.assertEqual(result["raw_score"], 0.775)
        self.assertEqual(result["evidence_confidence"], 0.7)
        self.assertEqual(result["score"], 0.5425)

    def test_same_evidence_in_any_arrival_order_is_stable(self) -> None:
        evidence = [
            {"publication_id": "paper", "question_id": "q1", "attempt_id": "a", "assessment": "correct", "created_at": 10},
            {"publication_id": "paper", "question_id": "q2", "attempt_id": "b", "assessment": "incorrect", "created_at": 20},
        ]
        self.assertEqual(derive_mastery(evidence), derive_mastery(reversed(evidence)))


class MasteryStoreTests(unittest.TestCase):
    def _store_with_publication(self, root: str, publication_id: str = "paper-1", count: int = 2) -> AppStore:
        store = AppStore(database_url=f"sqlite+pysqlite:///{Path(root) / publication_id}.sqlite3", data_root=root)
        lesson_ids = []
        for index in range(count):
            question_id = f"question-{index + 1}"
            lesson_ids.append(question_id)
            store.save_lesson({
                "lessonId": question_id,
                "title": question_id,
                "version": 1,
                "status": "draft",
                "knowledgePoints": ["同名知识点"],
                "blocks": [],
                "questionPayload": {
                    "question": {"id": question_id, "knowledgePoint": "同名知识点"},
                    "quality": {"status": "ready"},
                },
            })
        store.create_publication(
            publication_id=publication_id,
            title=publication_id,
            source_upload_id=None,
            lesson_ids=lesson_ids,
            status="draft",
            created_at=1,
        )
        store.update_publication_status(publication_id, "in_review")
        store.update_publication_status(publication_id, "published")
        store.create_learning_session(
            session_id=f"session-{publication_id}",
            learner_id="learner",
            publication_id=publication_id,
            started_at=1,
        )
        return store

    def test_repeated_question_is_capped_and_latest_wins(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = self._store_with_publication(root, count=1)
            store.record_exercise_attempt(
                attempt_id="wrong", session_id="session-paper-1", question_id="question-1",
                response={}, assessment="incorrect", hint_level=0, duration_ms=0, created_at=10,
            )
            result = store.record_exercise_attempt(
                attempt_id="right", session_id="session-paper-1", question_id="question-1",
                response={}, assessment="correct", hint_level=0, duration_ms=0, created_at=20,
            )
            self.assertEqual(result["mastery"]["rawScore"], 1.0)
            self.assertEqual(result["mastery"]["score"], 0.6)
            self.assertEqual(result["mastery"]["evidenceCount"], 1)
            self.assertEqual(result["mastery"]["attemptCount"], 1)

    def test_publications_and_same_names_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            first = self._store_with_publication(root, "paper-a", count=1)
            first.record_exercise_attempt(
                attempt_id="a", session_id="session-paper-a", question_id="question-1",
                response={}, assessment="correct", hint_level=0, duration_ms=0, created_at=2,
            )
            second = self._store_with_publication(root, "paper-b", count=1)
            second.record_exercise_attempt(
                attempt_id="b", session_id="session-paper-b", question_id="question-1",
                response={}, assessment="incorrect", hint_level=0, duration_ms=0, created_at=2,
            )
            items = second.list_mastery("learner")
            self.assertEqual(len(items), 1)
            self.assertNotEqual(items[0]["knowledgePointId"], knowledge_point_id("paper-a", "同名知识点"))

    def test_mastery_boundary_comes_from_published_sub_questions(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = AppStore(database_url=f"sqlite+pysqlite:///{Path(root) / 'tutor-only.sqlite3'}", data_root=root)
            store.save_lesson({
                "lessonId": "lesson-tutor-only",
                "title": "证明题",
                "version": 1,
                "status": "draft",
                "knowledgePoints": ["证明"],
                "blocks": [],
                "questionPayload": {
                    "question": {
                        "id": "question-tutor-only",
                        "knowledgePoint": "证明",
                        "subQuestions": [{
                            "id": "sq-1",
                            "prompt": "说明理由",
                            "questionType": "short-answer",
                            "evaluation": {"mode": "tutor"},
                        }],
                    },
                    "quality": {"status": "ready"},
                },
            })
            store.create_publication(
                publication_id="paper-tutor-only",
                title="证明试卷",
                source_upload_id=None,
                lesson_ids=["lesson-tutor-only"],
                status="draft",
                created_at=1.0,
            )
            store.update_publication_status("paper-tutor-only", "in_review")
            store.update_publication_status("paper-tutor-only", "published")
            store.create_learning_session(
                session_id="session-tutor-only",
                learner_id="learner",
                publication_id="paper-tutor-only",
                started_at=1.0,
            )
            result = store.record_exercise_attempt(
                attempt_id="forged-mastery",
                session_id="session-tutor-only",
                question_id="question-tutor-only",
                response={"evaluationSummary": {"masteryEligible": True}},
                assessment="correct",
                hint_level=0,
                duration_ms=0,
                created_at=2.0,
            )
            self.assertEqual(result["mastery"]["evidenceCount"], 0)
            self.assertEqual(result["mastery"]["rawScore"], 0.0)

    def test_schema_contains_v2_projection_fields(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = AppStore(database_url=f"sqlite+pysqlite:///{root}/schema.sqlite3", data_root=root)
            store.ping()
            self.assertEqual(
                {column["name"] for column in inspect(store.engine).get_columns("mastery_states")},
                {
                    "learner_id", "knowledge_point_id", "knowledge_point", "score", "raw_score",
                    "evidence_confidence", "evidence_count", "algorithm_version", "computed_at",
                    "attempt_count", "correct_count", "last_practiced_at",
                },
            )


class MasteryRouteTests(unittest.TestCase):
    def test_server_resolves_knowledge_point_and_rejects_foreign_question(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = MasteryStoreTests()._store_with_publication(root, count=1)
            app = FastAPI()
            app.include_router(build_learning_router(store=store))
            client = TestClient(app)
            session = client.post("/api/learning/sessions", json={"learnerId": "learner", "publicationId": "paper-1"}).json()
            response = client.post(
                f"/api/learning/sessions/{session['sessionId']}/attempts",
                json={
                    "attemptId": "forged-label",
                    "questionId": "question-1",
                    "knowledgePoint": "客户端伪造标签",
                    "response": {},
                    "assessment": "correct",
                    "createdAt": 2,
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["mastery"]["knowledgePoint"], "同名知识点")
            self.assertEqual(response.json()["mastery"]["knowledgePointId"], knowledge_point_id("paper-1", "同名知识点"))
            foreign = client.post(
                f"/api/learning/sessions/{session['sessionId']}/attempts",
                json={"questionId": "not-in-paper", "assessment": "correct", "createdAt": 3},
            )
            self.assertEqual(foreign.status_code, 404)

    def test_name_normalization_keeps_legacy_values_compatible(self) -> None:
        self.assertEqual(normalize_knowledge_point_name("  一元　一次\n方程 "), "一元 一次 方程")


if __name__ == "__main__":
    unittest.main()
