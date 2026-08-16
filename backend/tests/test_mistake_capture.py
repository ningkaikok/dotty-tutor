from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from api.routers.mistake_routes import build_mistake_router
from persistence.mistake_store import MistakeStore
from persistence.tutoring_store import TutoringStore
from persistence.variation_store import VariationStore


def fake_recognize(
    _source_path: Path,
    source_text: str,
    _asset_dir: Path,
    _asset_url_prefix: str,
) -> tuple[dict, list[dict], dict, dict]:
    prompt = source_text or "解方程 2x + 3 = 11"
    payload = {
        "question": {
            "id": "mistake-question-1",
            "questionType": "short-answer",
            "chapter": "一元一次方程",
            "knowledgePoint": "移项",
            "prompt": prompt,
            "givens": [],
            "options": [],
            "imageUrls": [],
        },
        "lessonSteps": [],
        "architecture": {},
        "modelRun": {"provider": "mock", "model": "fixture", "fallback": False},
    }
    return (
        payload,
        [{"level": 0, "hint": "先移项"}],
        {"provider": "manual", "mode": "fixture", "fallback": False},
        {"provider": "mock", "model": "fixture", "fallback": False},
    )


class MistakeCaptureApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.store = MistakeStore(
            database_url=f"sqlite+pysqlite:///{self.directory.name}/mistakes.sqlite3",
            data_root=self.directory.name,
        )
        self.cleared: list[tuple[str, str]] = []
        app = FastAPI()
        app.include_router(build_mistake_router(
            store=self.store,
            recognize=fake_recognize,
            archive_cleanup=self._clear_tutor,
        ))
        self.client = TestClient(app)

    def _clear_tutor(self, mistake_id: str, learner_id: str) -> int:
        self.cleared.append((mistake_id, learner_id))
        return 1

    def tearDown(self) -> None:
        self.client.close()
        self.store.close()
        self.directory.cleanup()

    def test_import_confirm_list_and_archive_mistake(self) -> None:
        response = self.client.post(
            "/api/mistakes/import",
            files={"file": ("equation.png", b"image-fixture", "image/png")},
            data={"sourceText": "解方程 x + 1 = 3", "originalAnswer": "x=1"},
        )
        self.assertEqual(response.status_code, 200)
        imported = response.json()
        self.assertEqual(imported["status"], "pending_confirmation")
        self.assertEqual(imported["questionPayload"]["question"]["prompt"], "解方程 x + 1 = 3")
        self.assertNotIn("sourceImagePath", imported)

        source = self.client.get(imported["sourceImageUrl"])
        self.assertEqual(source.status_code, 200)
        self.assertEqual(source.content, b"image-fixture")

        confirmation = {
            "prompt": "解方程 x + 1 = 3",
            "originalAnswer": "x=1",
            "subject": "数学",
            "gradeBand": "初中",
            "chapter": "一元一次方程",
            "knowledgePoint": "移项",
            "errorReason": "calculation",
            "notes": "移项后符号写错",
        }
        confirmed_response = self.client.patch(
            f"/api/mistakes/{imported['mistakeId']}",
            json=confirmation,
        )
        self.assertEqual(confirmed_response.status_code, 200)
        confirmed = confirmed_response.json()
        self.assertEqual(confirmed["status"], "unmastered")
        self.assertEqual(confirmed["errorReason"], "calculation")
        self.assertIsNotNone(confirmed["confirmedAt"])

        listed = self.client.get("/api/mistakes").json()["items"]
        self.assertEqual([item["mistakeId"] for item in listed], [imported["mistakeId"]])

        archived = self.client.patch(
            f"/api/mistakes/{imported['mistakeId']}/archive",
            json={"archived": True},
        )
        self.assertEqual(archived.json()["status"], "archived")
        self.assertEqual(self.cleared, [(imported["mistakeId"], "local-demo")])
        self.assertEqual(self.client.get("/api/mistakes").json()["items"], [])

    def test_rejects_non_image_upload(self) -> None:
        response = self.client.post(
            "/api/mistakes/import",
            files={"file": ("answer.txt", b"not-an-image", "text/plain")},
        )
        self.assertEqual(response.status_code, 415)

    def test_confirmation_rejects_unknown_error_reason(self) -> None:
        response = self.client.post(
            "/api/mistakes/import",
            files={"file": ("equation.png", b"image-fixture", "image/png")},
        )
        mistake_id = response.json()["mistakeId"]
        invalid = self.client.patch(
            f"/api/mistakes/{mistake_id}",
            json={
                "prompt": "题目",
                "chapter": "方程",
                "knowledgePoint": "移项",
                "errorReason": "guess",
            },
        )
        self.assertEqual(invalid.status_code, 422)

    def test_archive_keeps_learning_evidence_clears_thread_and_restore_starts_new_thread(self) -> None:
        """归档是错题软删除；陪练上下文清理，但验证证据必须可追溯。"""
        engine = create_engine(f"sqlite:///{self.directory.name}/archive.sqlite3", future=True)
        mistakes = MistakeStore(engine=engine, data_root=self.directory.name)
        tutoring = TutoringStore(engine=engine)
        variations = VariationStore(engine=engine)
        now = 1.0
        mistakes.create({
            "mistakeId": "archive-mistake",
            "learnerId": "local-demo",
            "sourceFilename": "source.png",
            "contentType": "image/png",
            "sourceImagePath": "",
            "sourceImageUrl": "",
            "questionPayload": {"question": {"id": "archive-question", "prompt": "题目"}},
            "guideCards": [], "ocrRun": {}, "modelRun": {}, "originalAnswer": "B",
            "chapter": "章节", "knowledgePoint": "知识点", "status": "unmastered",
            "confirmedAt": now, "createdAt": now, "updatedAt": now,
        })
        variation = variations.create(
            mistake_id="archive-mistake", learner_id="local-demo", strategy="foundation",
            level="basic", question_payload={"question": {"id": "variation-question"}}, model_run={},
        )
        answered = variations.answer(
            variation["variationId"], response={"selectedOptions": ["(A)"]},
            assessment="correct", feedback="验证正确",
        )
        thread = tutoring.create_or_get("archive-mistake", "local-demo")
        tutoring.append_turn(
            thread["threadId"], student_content="我的答案是 A", input_mode="text",
            assistant_content="我们继续验证", assessment="correct", action={}, model_run={},
            stage="verify", hint_level=0, summary="已完成一轮",
        )
        app = FastAPI()
        app.include_router(build_mistake_router(
            store=mistakes, recognize=fake_recognize, archive_cleanup=tutoring.delete_for_mistake,
        ))
        client = TestClient(app)
        try:
            archived = client.patch("/api/mistakes/archive-mistake/archive", json={"archived": True})
            self.assertEqual(archived.status_code, 200)
            self.assertEqual(archived.json()["status"], "archived")
            self.assertEqual(mistakes.get("archive-mistake")["questionPayload"]["question"]["id"], "archive-question")
            self.assertEqual(variations.get(variation["variationId"])["response"], answered["response"])
            self.assertEqual(variations.get(variation["variationId"])["assessment"], "correct")
            self.assertIsNone(tutoring.get(thread["threadId"]))

            restored = client.patch("/api/mistakes/archive-mistake/archive", json={"archived": False})
            self.assertEqual(restored.status_code, 200)
            new_thread = tutoring.create_or_get("archive-mistake", "local-demo")
            self.assertNotEqual(new_thread["threadId"], thread["threadId"])
            self.assertEqual(new_thread["messages"], [])
        finally:
            client.close()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
