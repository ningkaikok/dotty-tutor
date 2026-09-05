from __future__ import annotations

import time
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from persistence.mistake_store import MistakeStore
from persistence.review_store import ReviewStore
from persistence.tutoring_store import TutoringStore
from persistence.variation_store import VariationStore
from routers.practice_routes import build_practice_router
from tests.postgres_test_support import PostgresTestCase
from variation_service import VariationService


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
                "contentBlocks": [
                    {"id": "stem-1", "type": "text", "text": "下列各数中比 2 大的是？", "sourceOrder": 0},
                    {
                        "id": "options",
                        "type": "options",
                        "sourceOrder": 1,
                        "items": [
                            {
                                "label": f"({label})",
                                "contentBlocks": [
                                    {"id": f"option-{label}", "type": "text", "text": value, "sourceOrder": 0},
                                ],
                            }
                            for label, value in zip("ABCD", ["3", "0", "2", "-1"])
                        ],
                    },
                ],
            },
            "lessonSteps": [],
            "architecture": {},
            "modelRun": {"provider": "mock", "model": "demo", "fallback": False},
        },
        [],
        {"provider": "mock", "model": "demo", "fallback": False},
    )


class VariationPracticeTests(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.mistakes = MistakeStore(engine=self.engine, data_root=self.data_root)
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
        self.addCleanup(self.client.close)
        self._create_confirmed_mistake()

    def _create_confirmed_mistake(self) -> None:
        now = time.time()
        self.mistakes.create({
            "mistakeId": "mistake-1",
            "learnerId": "local-demo",
            "sourceFilename": "source.png",
            "contentType": "image/png",
            "sourceImagePath": str(self.data_root / "source.png"),
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
                    "contentBlocks": [
                        {"id": "stem-1", "type": "text", "text": "下列各数中比 1 大的是？", "sourceOrder": 0},
                        {
                            "id": "options",
                            "type": "options",
                            "sourceOrder": 1,
                            "items": [
                                {
                                    "label": f"({label})",
                                    "contentBlocks": [
                                        {"id": f"option-{label}", "type": "text", "text": value, "sourceOrder": 0},
                                    ],
                                }
                                for label, value in zip("ABCD", ["2", "0", "1", "-3"])
                            ],
                        },
                    ],
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

    def test_first_variation_correct_completes_single_question_mastery(self) -> None:
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
        self.assertEqual(answered.json()["mastery"]["requiredCorrect"], 1)
        self.assertTrue(answered.json()["mastery"]["mastered"])
        self.assertEqual(self.mistakes.get("mistake-1")["status"], "mastered")
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
        reused = self.client.post("/api/mistakes/mistake-1/variations")
        self.assertEqual(reused.status_code, 200)
        self.assertEqual(reused.json()["variationId"], created["variationId"])
        self.assertEqual(reused.json()["sequence"], 1)
        self.assertEqual(answered.json()["tutorStage"], "practice")
        self.assertEqual(self.threads.find_for_mistake("mistake-1", "local-demo")["stage"], "practice")

        retried = self.client.post(
            f"/api/variations/{created['variationId']}/answer",
            json={"content": "我改成选择 A", "interactionResult": {"selectedOptions": ["A"]}},
        )
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(retried.json()["assessment"], "correct")
        self.assertTrue(retried.json()["mastery"]["mastered"])
        self.assertEqual(self.mistakes.get("mistake-1")["status"], "mastered")

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

    def test_generation_prefers_gated_ai_error_reason(self) -> None:
        self._advance_thread_to_verify()
        self.mistakes.update_ai_error_reason(
            "mistake-1", category="reading", confidence=0.9
        )

        created = self.client.post("/api/mistakes/mistake-1/variations")

        self.assertEqual(created.status_code, 200)
        item = created.json()
        self.assertEqual(item["strategy"], "condition-reading")
        self.assertEqual(item["questionPayload"]["question"]["variationTarget"], "reading")
        self.assertEqual(item["attributionSource"], "ai")

    def test_generation_uses_self_then_unknown_without_rejecting(self) -> None:
        mistake = self.mistakes.get("mistake-1") or {}
        service = VariationService(generator=fake_generator)
        generated = service.generate(mistake, 1)
        self.assertEqual(generated["attributionSource"], "self")
        mistake["errorReason"] = None
        mistake["aiErrorReason"] = None
        unknown = service.generate(mistake, 1)
        self.assertEqual(unknown["attributionSource"], "unknown")
        self.assertEqual(unknown["strategy"], "scaffolded-transfer")
        mistake["errorReason"] = "concept"
        mistake["aiErrorReason"] = "unknown"
        self.assertEqual(service.generate(mistake, 1)["attributionSource"], "self")

    def test_generation_retries_once_and_self_heals_from_a_non_deterministic_type(self) -> None:
        mistake = self.mistakes.get("mistake-1") or {}
        calls: list[str] = []

        def flaky_generator(prompt: str) -> tuple[dict, list[dict], dict]:
            calls.append(prompt)
            if len(calls) == 1:
                return (
                    {"question": {"questionType": "short-answer", "prompt": "第一次生成的题干"}},
                    [],
                    {},
                )
            return (
                {"question": {"questionType": "true-false", "prompt": "第二次生成的题干"}},
                [],
                {},
            )

        service = VariationService(generator=flaky_generator)
        generated = service.generate(mistake, 1)

        self.assertEqual(len(calls), 2)
        self.assertIn("上一次生成不合格", calls[1])
        self.assertEqual(generated["questionPayload"]["question"]["questionType"], "true-false")

    def test_generation_raises_after_exhausting_retries_on_repeated_non_deterministic_type(
        self,
    ) -> None:
        mistake = self.mistakes.get("mistake-1") or {}
        calls: list[str] = []

        def always_subjective_generator(prompt: str) -> tuple[dict, list[dict], dict]:
            calls.append(prompt)
            return (
                {"question": {"questionType": "short-answer", "prompt": "简答题干"}},
                [],
                {},
            )

        service = VariationService(generator=always_subjective_generator)
        with self.assertRaises(ValueError):
            service.generate(mistake, 1)
        self.assertEqual(len(calls), 2)

    def test_incorrect_answer_can_be_resubmitted_without_generating_a_second_question(self) -> None:
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
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json()["assessment"], "correct")
        self.assertTrue(repeated.json()["mastery"]["mastered"])

        next_item = self.client.post("/api/mistakes/mistake-1/variations")
        self.assertEqual(next_item.status_code, 409)
        listed = self.client.get("/api/mistakes/mistake-1/variations").json()
        self.assertEqual(len(listed["items"]), 1)

        evidence = self.client.get("/api/mistakes/mistake-1/evidence")
        self.assertEqual(evidence.status_code, 200)
        attempts = evidence.json()["variations"][0]["attempts"]
        self.assertEqual(len(attempts), 2)
        self.assertEqual([attempt["assessment"] for attempt in attempts], ["incorrect", "correct"])
        self.assertEqual(evidence.json()["masteryTransition"], "unmastered → mastered")

    def test_answer_attempt_id_is_idempotent_and_does_not_append_twice(self) -> None:
        self._advance_thread_to_verify()
        item = self.client.post("/api/mistakes/mistake-1/variations").json()
        payload = {
            "attemptId": "stable-attempt-1",
            "content": "我选择 A",
            "interactionResult": {"selectedOptions": ["A"]},
        }
        first = self.client.post(f"/api/variations/{item['variationId']}/answer", json=payload)
        repeated = self.client.post(f"/api/variations/{item['variationId']}/answer", json=payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        attempts = self.client.get("/api/mistakes/mistake-1/evidence").json()["variations"][0]["attempts"]
        self.assertEqual(len(attempts), 1)

    def test_attempt_id_cannot_be_reused_for_another_variation(self) -> None:
        self._advance_thread_to_verify()
        item = self.client.post("/api/mistakes/mistake-1/variations").json()
        first = self.client.post(f"/api/variations/{item['variationId']}/answer", json={
            "attemptId": "shared-attempt",
            "content": "我选择 B",
            "interactionResult": {"selectedOptions": ["B"]},
        })
        self.assertEqual(first.status_code, 200)

        other = self.variations.create(
            mistake_id="mistake-1",
            learner_id="local-demo",
            strategy="concept-foundation",
            level="parallel",
            question_payload={"question": {
                "questionType": "choice",
                "prompt": "另一道题",
                "options": ["A", "B"],
                "correctAnswers": ["A"],
            }},
            model_run={},
        )
        conflict = self.client.post(f"/api/variations/{other['variationId']}/answer", json={
            "attemptId": "shared-attempt",
            "content": "我选择 A",
            "interactionResult": {"selectedOptions": ["A"]},
        })
        self.assertEqual(conflict.status_code, 409)

    def test_variation_requires_confirmed_error_reason_and_persists_strategy_metadata(self) -> None:
        self._advance_thread_to_verify()
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
        created = self.client.post("/api/mistakes/mistake-1/variations").json()
        question = created["questionPayload"]["question"]
        self.assertEqual(question["variationStrategyVersion"], "variation-strategy-v1")
        self.assertEqual(question["variationTarget"], "concept")
        self.assertTrue(question["variationObjective"])

    def test_one_correct_answer_promotes_mistake_and_schedules_reviews(self) -> None:
        self._advance_thread_to_verify()
        first = self.client.post("/api/mistakes/mistake-1/variations").json()
        first_result = self.client.post(f"/api/variations/{first['variationId']}/answer", json={
            "content": "我选择 A",
            "interactionResult": {"selectedOptions": ["A"]},
        }).json()
        self.assertEqual(first_result["mastery"]["correctStreak"], 1)
        self.assertEqual(first_result["mastery"]["requiredCorrect"], 1)
        self.assertTrue(first_result["mastery"]["mastered"])
        self.assertEqual(self.mistakes.get("mistake-1")["status"], "mastered")
        self.assertEqual([task["intervalDays"] for task in first_result["reviewTasks"]], [1, 3, 7])
        self.assertEqual(len(self.reviews.list_for_mistake("mistake-1")), 3)
        blocked = self.client.post("/api/mistakes/mistake-1/variations")
        self.assertEqual(blocked.status_code, 409)


if __name__ == "__main__":
    unittest.main()
