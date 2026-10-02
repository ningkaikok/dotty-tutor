from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from answer_evaluator import EVALUATOR_VERSION
from persistence.mistake_store import MistakeStore
from persistence.review_store import ReviewStore, review_metadata
from routers.review_routes import build_review_router
from tests.postgres_test_support import PostgresTestCase
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


class FailOnceReviewStore(ReviewStore):
    """Inject a post-completion scheduling failure for transaction tests."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.fail_once = True

    def _schedule_follow_up_in_connection(
        self,
        connection: Any,
        task: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        if self.fail_once:
            self.fail_once = False
            super()._schedule_follow_up_in_connection(connection, task, **kwargs)
            raise RuntimeError("injected follow-up failure")
        return super()._schedule_follow_up_in_connection(connection, task, **kwargs)


class ReviewStoreAtomicityTests(unittest.TestCase):
    def test_answer_retry_rolls_back_then_recovers_without_duplicate_evidence_or_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reviews.sqlite"
            engine = create_engine(f"sqlite:///{database}", future=True)
            review_metadata.create_all(engine)
            store = FailOnceReviewStore(engine=engine)
            task = store.schedule(
                mistake_id="mistake-atomic",
                learner_id="learner-atomic",
                base_time=1_000_000.0,
                objective_type="procedural",
            )[0]
            started = store.start(
                task["taskId"],
                question_payload={"question": {"prompt": "2 + 3"}},
                model_run={"provider": "fixture"},
            )
            self.assertIsNotNone(started)
            response = {"content": "4", "interactionResult": {"numericAnswer": "4"}}
            evidence = {
                "rubricPassed": False,
                "confidence": 1.0,
                "evidenceRefs": ["review:atomic:1"],
            }
            with self.assertRaisesRegex(RuntimeError, "injected follow-up failure"):
                store.answer_and_schedule_follow_up(
                    task["taskId"],
                    response=response,
                    assessment="incorrect",
                    feedback="再检查计算。",
                    evaluation_evidence=evidence,
                    next_action="stay",
                    trigger_evidence_ref="review:atomic:1",
                )

            rolled_back = store.get(task["taskId"])
            self.assertIsNotNone(rolled_back)
            self.assertEqual(rolled_back["status"], "ready")
            self.assertEqual(rolled_back["evaluationEvidence"], {})
            self.assertEqual(len(store.list_for_mistake("mistake-atomic")), 5)

            saved = store.answer_and_schedule_follow_up(
                task["taskId"],
                response=response,
                assessment="incorrect",
                feedback="再检查计算。",
                evaluation_evidence=evidence,
                next_action="stay",
                trigger_evidence_ref="review:atomic:1",
            )
            self.assertIsNotNone(saved)
            self.assertEqual(saved["status"], "completed")
            self.assertEqual(saved["evaluationEvidence"], evidence)
            tasks_after_save = store.list_for_mistake("mistake-atomic")
            self.assertEqual(len(tasks_after_save), 6)

            replay = store.answer_and_schedule_follow_up(
                task["taskId"],
                response=response,
                assessment="incorrect",
                feedback="再检查计算。",
                evaluation_evidence=evidence,
                next_action="stay",
                trigger_evidence_ref="review:atomic:1",
            )
            self.assertEqual(replay["taskId"], saved["taskId"])
            self.assertEqual(replay["evaluationEvidence"], evidence)
            self.assertEqual(len(store.list_for_mistake("mistake-atomic")), 6)

            legacy_task = store.schedule(
                mistake_id="mistake-legacy-replay",
                learner_id="learner-atomic",
                base_time=1_000_000.0,
                objective_type="procedural",
            )[0]
            store.start(
                legacy_task["taskId"],
                question_payload={"question": {"prompt": "2 + 3"}},
                model_run={"provider": "fixture"},
            )
            legacy_saved = store.answer(
                legacy_task["taskId"],
                response=response,
                assessment="incorrect",
                feedback="再检查计算。",
                evaluation_evidence=evidence,
            )
            self.assertIsNotNone(legacy_saved)
            recovered = store.answer_and_schedule_follow_up(
                legacy_task["taskId"],
                response=response,
                assessment="incorrect",
                feedback="再检查计算。",
                evaluation_evidence=evidence,
                next_action="stay",
                trigger_evidence_ref="review:atomic:1",
            )
            self.assertEqual(recovered["taskId"], legacy_task["taskId"])
            self.assertEqual(len(store.list_for_mistake("mistake-legacy-replay")), 6)
            store.close()


class SpacedReviewTests(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.mistakes = MistakeStore(engine=self.engine, data_root=self.data_root)
        self.reviews = ReviewStore(engine=self.engine)
        self._create_mastered_mistake()
        app = FastAPI()
        app.include_router(build_review_router(
            mistake_store=self.mistakes,
            review_store=self.reviews,
            variation_service=VariationService(generator=fake_generator),
        ))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def _create_mastered_mistake(self) -> None:
        now = time.time()
        self.mistakes.create({
            "mistakeId": "mistake-review",
            "learnerId": "local-demo",
            "sourceFilename": "source.png",
            "contentType": "image/png",
            "sourceImagePath": str(self.data_root / "source.png"),
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
        self.assertEqual([task["taskId"] for task in repeated], [task["taskId"] for task in first])
        self.assertEqual([task["intervalDays"] for task in first], [1, 3, 7])
        self.assertEqual(first[0]["dueAt"], anchor + 86_400)
        self.assertEqual(first[0]["scheduleVersion"], "legacy-1-3-7-v1")
        self.assertEqual(first[0]["sequenceNo"], 1)

    def test_review_api_exposes_policy_gate_and_next_action(self) -> None:
        self.reviews.schedule(
            mistake_id="mistake-review",
            learner_id="local-demo",
            base_time=time.time(),
        )
        listed = self.client.get("/api/reviews").json()["items"][0]
        self.assertEqual(listed["policy"]["gateMode"], "legacy")
        self.assertIn(listed["nextAction"], {"stay", "needs_review"})
        self.assertEqual(listed["gate"]["mode"], "legacy")

    def test_wrong_review_supersedes_future_tasks_and_starts_new_schedule_version(self) -> None:
        tasks = self.reviews.schedule(
            mistake_id="mistake-review",
            learner_id="local-demo",
            base_time=time.time() - 2 * 86_400,
            objective_type="procedural",
        )
        task = tasks[0]
        self.assertEqual(self.client.post(f"/api/reviews/{task['taskId']}/start").status_code, 200)
        answered = self.client.post(f"/api/reviews/{task['taskId']}/answer", json={
            "content": "4",
            "interactionResult": {"numericAnswer": "4"},
        })
        self.assertEqual(answered.status_code, 200)
        self.assertEqual(answered.json()["nextAction"], "stay")
        refreshed = self.reviews.list_for_mistake("mistake-review")
        self.assertTrue(any(item["status"] == "superseded" for item in refreshed))
        self.assertTrue(any(":retry-" in item["scheduleVersion"] for item in refreshed))
        retry = next(item for item in refreshed if ":retry-" in item["scheduleVersion"])
        repeated_retry = self.reviews.schedule_follow_up(
            task,
            assessment="incorrect",
            next_action="stay",
            base_time=time.time(),
        )
        self.assertIsNotNone(repeated_retry)
        self.assertEqual(repeated_retry["taskId"], retry["taskId"])
        self.assertEqual(repeated_retry["status"], "scheduled")

    def test_delayed_wrong_review_supersedes_overdue_open_tasks_but_keeps_history(self) -> None:
        tasks = self.reviews.schedule(
            mistake_id="mistake-review",
            learner_id="local-demo",
            base_time=time.time() - 10 * 86_400,
            objective_type="procedural",
        )
        first = tasks[0]
        self.assertLess(first["dueAt"], time.time())
        self.assertEqual(self.client.post(f"/api/reviews/{first['taskId']}/start").status_code, 200)
        answered = self.client.post(f"/api/reviews/{first['taskId']}/answer", json={
            "content": "4",
            "interactionResult": {"numericAnswer": "4"},
        })
        self.assertEqual(answered.status_code, 200)
        refreshed = self.reviews.list_for_mistake("mistake-review")
        initial_open = [
            item for item in refreshed
            if item["scheduleVersion"] == "mastery-policy-v1"
            and item["sequenceNo"] > first["sequenceNo"]
        ]
        self.assertTrue(initial_open)
        self.assertTrue(all(item["status"] == "superseded" for item in initial_open))
        self.assertTrue(all(item["supersededAt"] is not None for item in initial_open))
        completed_first = self.reviews.get(first["taskId"])
        self.assertIsNotNone(completed_first)
        self.assertEqual(completed_first["status"], "completed")
        self.assertIsNone(completed_first["supersededAt"])

    def test_wrong_then_retry_correct_below_accuracy_keeps_a_follow_up_task(self) -> None:
        tasks = self.reviews.schedule(
            mistake_id="mistake-review",
            learner_id="local-demo",
            base_time=time.time() - 2 * 86_400,
            objective_type="procedural",
        )
        first = tasks[0]
        self.assertEqual(self.client.post(f"/api/reviews/{first['taskId']}/start").status_code, 200)
        wrong = self.client.post(f"/api/reviews/{first['taskId']}/answer", json={
            "content": "4",
            "interactionResult": {"numericAnswer": "4"},
        })
        self.assertEqual(wrong.status_code, 200)

        retry = next(
            item for item in self.reviews.list_for_mistake("mistake-review")
            if ":retry-" in item["scheduleVersion"] and item["status"] == "scheduled"
        )
        self.assertEqual(self.client.post(f"/api/reviews/{retry['taskId']}/start").status_code, 200)
        correct = self.client.post(f"/api/reviews/{retry['taskId']}/answer", json={
            "content": "5",
            "interactionResult": {"numericAnswer": "5"},
        })
        self.assertEqual(correct.status_code, 200)
        self.assertEqual(correct.json()["nextAction"], "stay")
        follow_up = [
            item for item in self.reviews.list_for_mistake("mistake-review")
            if item["scheduleVersion"] == retry["scheduleVersion"]
            and item["sequenceNo"] == retry["sequenceNo"] + 1
        ]
        self.assertEqual(len(follow_up), 1)
        self.assertEqual(follow_up[0]["status"], "scheduled")

    def test_consecutive_wrong_retries_keep_policy_parseable_and_startable(self) -> None:
        tasks = self.reviews.schedule(
            mistake_id="mistake-review",
            learner_id="local-demo",
            base_time=time.time() - 2 * 86_400,
            objective_type="procedural",
        )
        first = tasks[0]
        self.assertEqual(self.client.post(f"/api/reviews/{first['taskId']}/start").status_code, 200)
        first_answer = self.client.post(f"/api/reviews/{first['taskId']}/answer", json={
            "content": "4",
            "interactionResult": {"numericAnswer": "4"},
        })
        self.assertEqual(first_answer.status_code, 200)
        retry_one = next(
            item for item in self.reviews.list_for_mistake("mistake-review")
            if item["scheduleVersion"] == "mastery-policy-v1:retry-2"
        )
        self.assertEqual(self.client.post(f"/api/reviews/{retry_one['taskId']}/start").status_code, 200)
        second_answer = self.client.post(f"/api/reviews/{retry_one['taskId']}/answer", json={
            "content": "4",
            "interactionResult": {"numericAnswer": "4"},
        })
        self.assertEqual(second_answer.status_code, 200)
        retry_two = next(
            item for item in self.reviews.list_for_mistake("mistake-review")
            if item["status"] == "scheduled" and ":retry-3" in item["scheduleVersion"]
        )
        self.assertNotIn(":retry-2:retry-2", retry_two["scheduleVersion"])
        self.assertEqual(self.client.get("/api/reviews").status_code, 200)
        self.assertEqual(self.client.post(f"/api/reviews/{retry_two['taskId']}/start").status_code, 200)

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
        self.assertEqual(evidence["evaluatorVersion"], EVALUATOR_VERSION)
        stored = self.reviews.get(task["taskId"])
        self.assertIsNotNone(stored)
        self.assertEqual(stored["evaluationEvidence"], evidence)
        listed = self.reviews.list_for_learner("local-demo")
        self.assertEqual(listed[0]["evaluationEvidence"], evidence)


if __name__ == "__main__":
    unittest.main()
