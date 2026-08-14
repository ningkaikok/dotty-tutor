from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from mistake_store import MistakeStore
from practice_routes import build_practice_router
from review_store import ReviewStore
from tutoring_store import TutoringStore
from variation_service import VariationService
from variation_store import VariationStore


def fake_generator(source_text: str) -> tuple[dict, list[dict], dict]:
    """Return a stable question so this test verifies orchestration, not a model."""
    return (
        {
            "question": {
                "id": "variation-question",
                "questionType": "choice",
                "prompt": "下列各数中比 2 大的是？",
                "options": ["3", "0", "2", "-1"],
                "correctAnswers": ["A"],
                "chapter": "有理数",
                "knowledgePoint": "数的比较",
                "givens": [],
            },
            "lessonSteps": [],
            "architecture": {},
            "modelRun": {"provider": "mock", "model": "demo", "fallback": False},
        },
        [],
        {"provider": "mock", "model": "demo", "fallback": False},
    )


class VariationPracticeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        database = Path(self.temporary.name) / "practice.sqlite3"
        self.engine = create_engine(f"sqlite:///{database}", future=True)
        self.mistakes = MistakeStore(engine=self.engine, data_root=self.temporary.name)
        self.threads = TutoringStore(engine=self.engine)
        self.variations = VariationStore(engine=self.engine)
        self.reviews = ReviewStore(engine=self.engine)
        app = FastAPI()
        app.include_router(build_practice_router(
            mistake_store=self.mistakes,
            tutoring_store=self.threads,
            variation_store=self.variations,
            variation_service=VariationService(generator=fake_generator),
            review_store=self.reviews,
        ))
        self.client = TestClient(app)
        self._create_confirmed_mistake()

    def tearDown(self) -> None:
        self.client.close()
        self.engine.dispose()
        self.temporary.cleanup()

    def _create_confirmed_mistake(self) -> None:
        now = time.time()
        self.mistakes.create({
            "mistakeId": "mistake-1",
            "learnerId": "local-demo",
            "sourceFilename": "source.png",
            "contentType": "image/png",
            "sourceImagePath": str(Path(self.temporary.name) / "source.png"),
            "sourceImageUrl": "/api/mistakes/mistake-1/source",
            "questionPayload": {
                "question": {
                    "id": "question-1",
                    "questionType": "choice",
                    "prompt": "下列各数中比 1 大的是？",
                    "options": ["2", "0", "1", "-3"],
                    "correctAnswers": ["A"],
                    "chapter": "有理数",
                    "knowledgePoint": "数的比较",
                    "givens": [],
                },
                "lessonSteps": [],
                "modelRun": {},
            },
            "guideCards": [],
            "ocrRun": {},
            "modelRun": {},
            "originalAnswer": "B",
            "chapter": "有理数",
            "knowledgePoint": "数的比较",
            "status": "pending_confirmation",
            "createdAt": now,
            "updatedAt": now,
        })
        self.mistakes.confirm("mistake-1", {
            "prompt": "下列各数中比 1 大的是？",
            "originalAnswer": "B",
            "subject": "数学",
            "gradeBand": "初中",
            "chapter": "有理数",
            "knowledgePoint": "数的比较",
            "errorReason": "concept",
            "notes": "",
        })

    def _advance_thread(self, stage: str = "verify") -> None:
        thread = self.threads.create_or_get("mistake-1", "local-demo")
        self.threads.append_turn(
            thread["threadId"],
            student_content="我已经完成陪练",
            input_mode="text",
            assistant_content="可以开始验证。",
            assessment="correct",
            action={"type": "advance_stage"},
            model_run={},
            stage=stage,
            hint_level=0,
            summary="完成单题陪练",
        )

    def _advance_thread_to_verify(self) -> None:
        self._advance_thread("verify")

    def test_practice_thread_can_generate_first_variation_and_advances_on_correct(self) -> None:
        self._advance_thread("practice")
        created = self.client.post("/api/mistakes/mistake-1/variations")
        self.assertEqual(created.status_code, 200)

        answered = self.client.post(
            f"/api/variations/{created.json()['variationId']}/answer",
            json={"content": "我选择 A", "interactionResult": {"selectedOptions": ["A"]}},
        )

        self.assertEqual(answered.status_code, 200)
        self.assertEqual(answered.json()["assessment"], "correct")
        self.assertEqual(answered.json()["tutorStage"], "verify")
        self.assertEqual(self.threads.find_for_mistake("mistake-1", "local-demo")["stage"], "verify")

    def test_incorrect_first_variation_keeps_practice_stage(self) -> None:
        self._advance_thread("practice")
        created = self.client.post("/api/mistakes/mistake-1/variations").json()

        answered = self.client.post(
            f"/api/variations/{created['variationId']}/answer",
            json={"content": "我选择 B", "interactionResult": {"selectedOptions": ["B"]}},
        )

        self.assertEqual(answered.status_code, 200)
        self.assertEqual(answered.json()["assessment"], "incorrect")
        self.assertEqual(answered.json()["tutorStage"], "practice")
        self.assertEqual(self.threads.find_for_mistake("mistake-1", "local-demo")["stage"], "practice")

    def test_generation_requires_completed_tutoring_and_uses_error_strategy(self) -> None:
        blocked = self.client.post("/api/mistakes/mistake-1/variations")
        self.assertEqual(blocked.status_code, 409)

        self._advance_thread_to_verify()
        created = self.client.post("/api/mistakes/mistake-1/variations")

        self.assertEqual(created.status_code, 200)
        item = created.json()
        self.assertEqual(item["strategy"], "concept-foundation")
        self.assertEqual(item["level"], "foundation")
        self.assertEqual(item["questionPayload"]["question"]["variationOf"], "question-1")

    def test_answer_is_deterministic_and_cannot_be_submitted_twice(self) -> None:
        self._advance_thread_to_verify()
        item = self.client.post("/api/mistakes/mistake-1/variations").json()

        answered = self.client.post(f"/api/variations/{item['variationId']}/answer", json={
            "content": "我选择 B",
            "interactionResult": {"selectedOptions": ["B"]},
        })
        repeated = self.client.post(f"/api/variations/{item['variationId']}/answer", json={
            "content": "改成 A",
            "interactionResult": {"selectedOptions": ["A"]},
        })

        self.assertEqual(answered.status_code, 200)
        self.assertEqual(answered.json()["assessment"], "incorrect")
        self.assertEqual(repeated.status_code, 409)

        next_item = self.client.post("/api/mistakes/mistake-1/variations").json()
        self.assertEqual(next_item["sequence"], 2)
        self.assertEqual(next_item["level"], "parallel")
        listed = self.client.get("/api/mistakes/mistake-1/variations").json()
        self.assertEqual(len(listed["items"]), 2)

    def test_two_consecutive_correct_answers_promote_mistake(self) -> None:
        self._advance_thread_to_verify()
        first = self.client.post("/api/mistakes/mistake-1/variations").json()
        first_result = self.client.post(f"/api/variations/{first['variationId']}/answer", json={
            "content": "我选择 A",
            "interactionResult": {"selectedOptions": ["A"]},
        }).json()
        self.assertEqual(first_result["mastery"]["correctStreak"], 1)
        self.assertFalse(first_result["mastery"]["mastered"])
        self.assertEqual(self.mistakes.get("mistake-1")["status"], "unmastered")

        second = self.client.post("/api/mistakes/mistake-1/variations").json()
        second_result = self.client.post(f"/api/variations/{second['variationId']}/answer", json={
            "content": "我仍然选择 A",
            "interactionResult": {"selectedOptions": ["A"]},
        }).json()

        self.assertEqual(second_result["mastery"]["correctStreak"], 2)
        self.assertTrue(second_result["mastery"]["mastered"])
        self.assertEqual(self.mistakes.get("mistake-1")["status"], "mastered")
        self.assertEqual([task["intervalDays"] for task in second_result["reviewTasks"]], [1, 3, 7])
        self.assertEqual(len(self.reviews.list_for_mistake("mistake-1")), 3)
        blocked = self.client.post("/api/mistakes/mistake-1/variations")
        self.assertEqual(blocked.status_code, 409)


if __name__ == "__main__":
    unittest.main()
