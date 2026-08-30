from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from persistence.mistake_store import MistakeStore
from persistence.review_store import ReviewStore
from routers.review_routes import build_review_router
from variation_service import VariationService


def fake_generator(source_text: str) -> tuple[dict, list[dict], dict]:
    return (
        {
            "question": {
                "id": "review-question",
                "questionType": "numeric",
                "prompt": "计算 2 + 3。",
                "answerSpec": {"answerType": "numeric", "expected": "5", "tolerance": 0},
                "chapter": "有理数",
                "knowledgePoint": "基础运算",
                "givens": [],
                "contentBlocks": [
                    {"id": "stem-1", "type": "text", "text": "计算 2 + 3。", "sourceOrder": 0},
                ],
            },
            "lessonSteps": [],
            "architecture": {},
            "modelRun": {"provider": "mock", "model": "demo", "fallback": False},
        },
        [],
        {"provider": "mock", "model": "demo", "fallback": False},
    )


class SpacedReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        database = Path(self.temporary.name) / "review.sqlite3"
        self.engine = create_engine(f"sqlite:///{database}", future=True)
        self.mistakes = MistakeStore(engine=self.engine, data_root=self.temporary.name)
        self.reviews = ReviewStore(engine=self.engine)
        self._create_mastered_mistake()
        app = FastAPI()
        app.include_router(build_review_router(
            mistake_store=self.mistakes,
            review_store=self.reviews,
            variation_service=VariationService(generator=fake_generator),
        ))
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.engine.dispose()
        self.temporary.cleanup()

    def _create_mastered_mistake(self) -> None:
        now = time.time()
        self.mistakes.create({
            "mistakeId": "mistake-review",
            "learnerId": "local-demo",
            "sourceFilename": "source.png",
            "contentType": "image/png",
            "sourceImagePath": str(Path(self.temporary.name) / "source.png"),
            "sourceImageUrl": "/source.png",
            "questionPayload": {
                "question": {
                    "id": "original",
                    "questionType": "numeric",
                    "prompt": "计算 1 + 2。",
                    "answerSpec": {"answerType": "numeric", "expected": "3"},
                    "chapter": "有理数",
                    "knowledgePoint": "基础运算",
                    "givens": [],
                },
                "lessonSteps": [],
                "modelRun": {},
            },
            "guideCards": [],
            "ocrRun": {},
            "modelRun": {},
            "chapter": "有理数",
            "knowledgePoint": "基础运算",
            "errorReason": "calculation",
            "status": "unmastered",
            "createdAt": now,
            "updatedAt": now,
        })
        self.mistakes.mark_mastered("mistake-review")

    def test_schedule_is_idempotent_and_uses_fixed_intervals(self) -> None:
        anchor = 1_000_000.0
        first = self.reviews.schedule(
            mistake_id="mistake-review",
            learner_id="local-demo",
            base_time=anchor,
        )
        repeated = self.reviews.schedule(
            mistake_id="mistake-review",
            learner_id="local-demo",
            base_time=anchor,
        )

        self.assertEqual(len(first), 3)
        self.assertEqual(len(repeated), 3)
        self.assertEqual([task["intervalDays"] for task in first], [1, 3, 7])
        self.assertEqual(first[0]["dueAt"], anchor + 86_400)

    def test_review_question_can_be_started_answered_and_reported(self) -> None:
        task = self.reviews.schedule(
            mistake_id="mistake-review",
            learner_id="local-demo",
            base_time=time.time() - 2 * 86_400,
        )[0]

        started = self.client.post(f"/api/reviews/{task['taskId']}/start")
        answered = self.client.post(f"/api/reviews/{task['taskId']}/answer", json={
            "content": "5",
            "interactionResult": {"numericAnswer": "5"},
        })
        progress = self.client.get("/api/progress").json()

        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.json()["status"], "ready")
        self.assertEqual(answered.status_code, 200)
        self.assertEqual(answered.json()["assessment"], "correct")
        self.assertEqual(progress["masteredCount"], 1)
        self.assertEqual(progress["completedReviewCount"], 1)
        self.assertEqual(progress["reviewAccuracy"], 1)
        self.assertEqual(progress["knowledgePoints"][0]["knowledgePoint"], "基础运算")

    def test_review_evaluation_evidence_is_persisted_and_listed(self) -> None:
        task = self.reviews.schedule(
            mistake_id="mistake-review",
            learner_id="local-demo",
            base_time=time.time() - 2 * 86_400,
        )[0]

        started = self.client.post(f"/api/reviews/{task['taskId']}/start")
        self.assertEqual(started.status_code, 200)
        answered = self.client.post(f"/api/reviews/{task['taskId']}/answer", json={
            "content": "5",
            "interactionResult": {"numericAnswer": "5"},
        })

        self.assertEqual(answered.status_code, 200)
        evidence = answered.json()["evaluationEvidence"]
        self.assertEqual(evidence["strategy"], "numeric-tolerance")
        self.assertEqual(evidence["evaluatorVersion"], "answer-evaluator-v1")
        stored = self.reviews.get(task["taskId"])
        self.assertIsNotNone(stored)
        self.assertEqual(stored["evaluationEvidence"], evidence)
        listed = self.reviews.list_for_learner("local-demo")
        self.assertEqual(listed[0]["evaluationEvidence"], evidence)


if __name__ == "__main__":
    unittest.main()
