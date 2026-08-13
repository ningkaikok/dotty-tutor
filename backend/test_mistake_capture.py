from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mistake_routes import build_mistake_router
from mistake_store import MistakeStore


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


if __name__ == "__main__":
    unittest.main()
