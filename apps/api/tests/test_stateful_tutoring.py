from __future__ import annotations

import time
import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from application.services.stateful_tutor import StatefulTutor
from domain.contracts.tutoring import TutorMessageRequest
from domain.questions.contracts import TutorReply
from persistence.mistake_store import MistakeStore
from persistence.tutoring_store import TutoringStore
from routers.tutoring_routes import build_tutoring_router
from tests.postgres_test_support import PostgresTestCase

GUIDE_CARD = {
    "level": 0,
    "stuckAt": "没有把不等式条件和选项对应起来。",
    "knowledge": ["有理数比较"],
    "hint": "把每个数放到数轴上比较。",
    "question": "哪个数在 1 的右侧？",
    "canvasAction": "show-base",
}


class _GeneratedRuntime:
    def __init__(self, generated: dict) -> None:
        self.selection = SimpleNamespace(provider="codex", model="test-model")
        self.generated = generated
        self.prompts: list[str] = []

    def generate_json(self, prompt: str, schema: dict, max_tokens: int = 450):
        self.prompts.append(prompt)
        return self.generated, {
            "requestedProvider": "codex",
            "provider": "codex",
            "model": "test-model",
            "fallback": False,
        }


class _PlanlessTutor:
    def reply(self, *, thread: dict, **_: object) -> dict:
        return {
            "reply": TutorReply(
                reply="请先说说你卡住的步骤。",
                guideContext={"assessment": "partial"},
                nextHintLevel=0,
                canvasAction="show-base",
                source="stored-guide-card",
            ),
            "stage": thread["stage"],
            "action": {"assessment": "partial", "tutorTurnPlan": {}},
            "summary": thread.get("summary", ""),
            "inputMode": "text",
        }


class StatefulTutoringTests(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.mistakes = MistakeStore(engine=self.engine, data_root=self.data_root)
        self.threads = TutoringStore(engine=self.engine)
        runtime = SimpleNamespace(selection=SimpleNamespace(provider="mock", model="demo"))
        app = FastAPI()
        app.include_router(build_tutoring_router(
            mistake_store=self.mistakes,
            tutoring_store=self.threads,
            tutor=StatefulTutor(runtime=runtime),
        ))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def _mistake(self, *, confirmed: bool = True) -> dict:
        now = time.time()
        item = self.mistakes.create({
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
                "modelRun": {"provider": "mock", "model": "demo", "fallback": False},
            },
            "guideCards": [GUIDE_CARD],
            "ocrRun": {},
            "modelRun": {},
            "originalAnswer": "B",
            "subject": "数学",
            "gradeBand": "初中",
            "chapter": "有理数",
            "knowledgePoint": "数的比较",
            "status": "pending_confirmation",
            "createdAt": now,
            "updatedAt": now,
        })
        if not confirmed:
            return item
        return self.mistakes.confirm("mistake-1", {
            "prompt": "下列各数中比 1 大的是？",
            "originalAnswer": "B",
            "subject": "数学",
            "gradeBand": "初中",
            "chapter": "有理数",
            "knowledgePoint": "数的比较",
            "errorReason": "concept",
            "notes": "",
        }) or item

    def test_thread_persists_turns_and_advances_with_deterministic_checks(self) -> None:
        self._mistake()
        created = self.client.post("/api/mistakes/mistake-1/thread")
        self.assertEqual(created.status_code, 200)
        thread_id = created.json()["threadId"]
        self.assertEqual(created.json()["stage"], "diagnose")
        restored_create = self.client.post("/api/mistakes/mistake-1/thread").json()
        self.assertEqual(restored_create["threadId"], thread_id)
        self.assertEqual(restored_create["messages"], [])

        incorrect = self.client.post(f"/api/tutor/threads/{thread_id}/messages", json={
            "content": "我选择 B",
            "mode": "answer",
            "interactionResult": {"selectedOptions": ["B"]},
        })
        self.assertEqual(incorrect.status_code, 200)
        self.assertEqual(incorrect.json()["action"]["assessment"], "incorrect")
        self.assertEqual(incorrect.json()["thread"]["stage"], "explain")
        # 旧 action 字段仍在，同时新审计字段可供恢复线程和排障使用。
        action = incorrect.json()["action"]
        self.assertEqual(action["previousStage"], "diagnose")
        self.assertEqual(action["tutorTurnPlan"]["errorStrategy"]["id"], "concept-foundation")
        self.assertEqual(action["tutorTurnPlan"]["intent"]["id"], "submit-answer")
        self.assertEqual(action["tutorTurnPlan"]["teachingAction"], "inspect-first-error")
        self.assertTrue(action["tutorTurnPlan"]["misconception"]["needsConfirmation"])
        self.assertEqual(action["modelRun"]["provider"], "mock")
        self.assertEqual(action["deduplication"]["retryCount"], 0)
        self.assertIn("modelRun", incorrect.json()["reply"])

        correct = self.client.post(f"/api/tutor/threads/{thread_id}/messages", json={
            "content": "我重新选择 A",
            "mode": "answer",
            "interactionResult": {"selectedOptions": ["A"]},
        })
        self.assertEqual(correct.status_code, 200)
        self.assertEqual(correct.json()["action"]["assessment"], "correct")
        self.assertEqual(correct.json()["thread"]["stage"], "practice")

        verified = self.client.post(f"/api/tutor/threads/{thread_id}/messages", json={
            "content": "我能再次判断为 A",
            "mode": "answer",
            "interactionResult": {"selectedOptions": ["A"]},
        })
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(verified.json()["thread"]["stage"], "verify")

        restored = self.client.get(f"/api/tutor/threads/{thread_id}").json()
        self.assertEqual(restored["messageCount"], 6)
        self.assertEqual([message["role"] for message in restored["messages"]], [
            "student", "assistant", "student", "assistant", "student", "assistant",
        ])
        self.assertIn("diagnose→explain", restored["summary"])

    def test_pending_mistake_must_be_confirmed(self) -> None:
        self._mistake(confirmed=False)
        response = self.client.post("/api/mistakes/mistake-1/thread")
        self.assertEqual(response.status_code, 409)
        self.assertIn("请先确认", response.json()["detail"])

    def test_guided_answer_cannot_regress_practice_stage(self) -> None:
        self._mistake()
        thread_id = self.client.post("/api/mistakes/mistake-1/thread").json()["threadId"]
        self.client.post(f"/api/tutor/threads/{thread_id}/messages", json={
            "content": "我选择 B",
            "mode": "answer",
            "interactionResult": {"selectedOptions": ["B"]},
        })
        correct = self.client.post(f"/api/tutor/threads/{thread_id}/messages", json={
            "content": "我重新选择 A",
            "mode": "answer",
            "interactionResult": {"selectedOptions": ["A"]},
        })
        self.assertEqual(correct.json()["thread"]["stage"], "practice")

        guided = self.client.post(f"/api/tutor/threads/{thread_id}/messages", json={
            "content": "为什么还要再检查这一步？",
            "mode": "answer",
            "interactionResult": {},
        })

        self.assertEqual(guided.status_code, 200)
        self.assertEqual(guided.json()["action"]["assessment"], "partial")
        self.assertEqual(guided.json()["thread"]["stage"], "practice")

    def test_empty_structured_answer_is_rejected(self) -> None:
        self._mistake()
        thread = self.client.post("/api/mistakes/mistake-1/thread").json()

        response = self.client.post(
            f"/api/tutor/threads/{thread['threadId']}/messages",
            json={
                "content": "",
                "mode": "answer",
                "interactionResult": {"selectedOptions": []},
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "请先输入或选择答案")

    def test_ready_confirmation_enters_verification_instead_of_repeating_hint(self) -> None:
        self._mistake()
        thread = self.client.post("/api/mistakes/mistake-1/thread").json()
        response = self.client.post(
            f"/api/tutor/threads/{thread['threadId']}/messages",
            json={"content": "准备好了", "mode": "answer", "interactionResult": {}},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["thread"]["stage"], "practice")
        self.assertIn("变式练习", response.json()["reply"]["reply"])
        self.assertNotIn("卡在", response.json()["reply"]["reply"])

    def test_gated_ai_attribution_is_persisted_and_unconfirmed_round_preserves_it(self) -> None:
        self._mistake()
        runtime = _GeneratedRuntime({
            "assessment": "partial",
            "reply": "我们先核对这一步。",
            "stuckAt": "计算步骤不完整",
            "knowledge": ["数的比较"],
            "hint": "写出中间计算步骤。",
            "question": "你漏写了哪一步？",
            "canvasAction": "show-base",
            "misconception": {
                "hypothesis": "学生漏掉了计算步骤",
                "evidence": "我不知道为什么要这样比较",
                "confidence": 0.9,
                "needsConfirmation": False,
                "category": "missing_step",
            },
        })
        app = FastAPI()
        app.include_router(build_tutoring_router(
            mistake_store=self.mistakes,
            tutoring_store=self.threads,
            tutor=StatefulTutor(runtime=runtime),
        ))
        client = TestClient(app)
        try:
            thread_id = client.post("/api/mistakes/mistake-1/thread").json()["threadId"]
            first = client.post(f"/api/tutor/threads/{thread_id}/messages", json={
                "content": "我不知道为什么要这样比较",
                "mode": "help",
            })
            self.assertEqual(first.status_code, 200)
            stored = self.mistakes.get("mistake-1")
            self.assertEqual(stored["aiErrorReason"], "missing_step")
            self.assertEqual(stored["aiErrorReasonConfidence"], 0.9)
            self.assertEqual(stored["errorReason"], "concept")
            latest = self.mistakes.latest_attribution("mistake-1")
            self.assertEqual(latest["source"], "ai")
            self.assertEqual(latest["category"], "missing_step")
            self.assertEqual(latest["evidence"]["matched"], True)

            runtime.generated["misconception"] = {
                "hypothesis": "学生可能漏看了条件",
                "evidence": "我不知道为什么要这样比较",
                "confidence": 0.4,
                "needsConfirmation": False,
                "category": "reading",
            }
            second = client.post(f"/api/tutor/threads/{thread_id}/messages", json={
                "content": "我不知道为什么要这样比较",
                "mode": "help",
            })
            self.assertEqual(second.status_code, 200)
            preserved = self.mistakes.get("mistake-1")
            self.assertEqual(preserved["aiErrorReason"], "missing_step")
            self.assertEqual(preserved["aiErrorReasonConfidence"], 0.9)
        finally:
            client.close()

    def test_unknown_ai_attribution_uses_self_strategy_and_is_not_persisted(self) -> None:
        self._mistake()
        runtime = _GeneratedRuntime({
            "assessment": "partial",
            "reply": "我们先核对这一步。",
            "stuckAt": "计算步骤不完整",
            "knowledge": ["数的比较"],
            "hint": "写出中间计算步骤。",
            "question": "你漏写了哪一步？",
            "canvasAction": "show-base",
            "misconception": {
                "hypothesis": "学生可能没有理解这一步",
                "evidence": "我不知道为什么要这样比较",
                "confidence": 0.9,
                "needsConfirmation": False,
                "category": "unknown",
            },
        })
        app = FastAPI()
        app.include_router(build_tutoring_router(
            mistake_store=self.mistakes,
            tutoring_store=self.threads,
            tutor=StatefulTutor(runtime=runtime),
        ))
        client = TestClient(app)
        try:
            thread_id = client.post("/api/mistakes/mistake-1/thread").json()["threadId"]
            response = client.post(f"/api/tutor/threads/{thread_id}/messages", json={
                "content": "我不知道为什么要这样比较",
                "mode": "help",
            })
            self.assertEqual(response.status_code, 200)
            plan = response.json()["action"]["tutorTurnPlan"]
            self.assertEqual(plan["errorStrategy"]["reason"], "concept")
            self.assertEqual(plan["errorStrategy"]["source"], "self")
            stored = self.mistakes.get("mistake-1")
            self.assertIsNone(stored["aiErrorReason"])
            self.assertIsNone(stored["aiErrorReasonConfidence"])
        finally:
            client.close()

    def test_generation_prompt_uses_persisted_ai_attribution_before_self_assessment(self) -> None:
        self._mistake()
        self.mistakes.update_ai_error_reason(
            "mistake-1", category="reading", confidence=0.9
        )
        runtime = _GeneratedRuntime({
            "assessment": "partial",
            "reply": "我们先重新提取题目条件。",
            "stuckAt": "条件提取不完整",
            "knowledge": ["数的比较"],
            "hint": "圈出题目中的比较对象。",
            "question": "题目要求比较哪些数？",
            "canvasAction": "show-base",
            "misconception": {},
        })
        thread = self.threads.create_or_get("mistake-1")
        StatefulTutor(runtime=runtime).reply(
            mistake=self.mistakes.get("mistake-1") or {},
            thread=thread,
            recent_messages=[],
            request=TutorMessageRequest(content="给我一点提示", mode="help"),
        )
        self.assertEqual(len(runtime.prompts), 1)
        self.assertIn("condition-reading", runtime.prompts[0])
        self.assertNotIn("concept-foundation", runtime.prompts[0])

    def test_missing_misconception_in_plan_does_not_write_ai_attribution(self) -> None:
        self._mistake()
        app = FastAPI()
        app.include_router(build_tutoring_router(
            mistake_store=self.mistakes,
            tutoring_store=self.threads,
            tutor=_PlanlessTutor(),
        ))
        client = TestClient(app)
        try:
            thread_id = client.post("/api/mistakes/mistake-1/thread").json()["threadId"]
            response = client.post(f"/api/tutor/threads/{thread_id}/messages", json={
                "content": "我还是不明白",
                "mode": "help",
            })
            self.assertEqual(response.status_code, 200)
            stored = self.mistakes.get("mistake-1")
            self.assertIsNone(stored["aiErrorReason"])
            self.assertIsNone(stored["aiErrorReasonConfidence"])
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
