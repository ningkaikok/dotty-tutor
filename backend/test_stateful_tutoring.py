from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from mistake_store import MistakeStore
from stateful_tutor import StatefulTutor
from tutoring_routes import build_tutoring_router
from tutoring_store import TutoringStore


GUIDE_CARD = {
    "level": 0,
    "stuckAt": "没有把不等式条件和选项对应起来。",
    "knowledge": ["有理数比较"],
    "hint": "把每个数放到数轴上比较。",
    "question": "哪个数在 1 的右侧？",
    "canvasAction": "show-base",
}


class StatefulTutoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        database = Path(self.temporary.name) / "tutoring.sqlite3"
        self.engine = create_engine(f"sqlite:///{database}", future=True)
        self.mistakes = MistakeStore(engine=self.engine, data_root=self.temporary.name)
        self.threads = TutoringStore(engine=self.engine)
        runtime = SimpleNamespace(selection=SimpleNamespace(provider="mock", model="demo"))
        app = FastAPI()
        app.include_router(build_tutoring_router(
            mistake_store=self.mistakes,
            tutoring_store=self.threads,
            tutor=StatefulTutor(runtime=runtime),
        ))
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.engine.dispose()
        self.temporary.cleanup()

    def _mistake(self, *, confirmed: bool = True) -> dict:
        now = time.time()
        item = self.mistakes.create({
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


if __name__ == "__main__":
    unittest.main()
