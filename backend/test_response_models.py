"""Exercise response_model= through the real HTTP layer.

Regular unittest cases in this repo mostly call route functions directly as
plain Python, which never triggers FastAPI's response_model serialization —
a mismatch between what a handler returns and its declared response_model
only surfaces at that layer (as a 500 ResponseValidationError). These tests
go through TestClient specifically to catch that class of bug.

storage.store is a module-level singleton bound once, at each router
factory's call time, to whatever database the ambient environment points
at when app.py is first imported — which in a full `unittest discover` run
may be the developer's real local/Docker Postgres. Tests that only *read*
(catalog endpoints) are harmless either way. Tests that *write* run in a
subprocess with an isolated DOTTY_DATA_DIR so they never touch real data
and their exact-count assertions stay valid regardless of what else is in
the real database.
"""

from __future__ import annotations

import base64
import io
import subprocess
import sys
import textwrap
import unittest
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

import app
from model_runtime import ModelSelection, runtime


# 1x1 transparent PNG, valid enough for the lightweight image import path.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def run_isolated(script: str, directory: str) -> str:
    """Run a script in a fresh interpreter with DOTTY_DATA_DIR isolated.

    A subprocess (not importlib.reload) is required: storage.store is bound
    once, at import time, into every router factory's closures — reload only
    rebinds the storage module's own attribute, not those closures.
    """
    import os

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(app.__file__.rsplit("/", 1)[0]),
        env={**os.environ, "DOTTY_DATA_DIR": directory, "MODEL_PROVIDER": "mock"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise AssertionError(f"isolated script failed:\n{completed.stdout}\n{completed.stderr}")
    return completed.stdout


class ReadOnlyEndpointResponseModelTests(unittest.TestCase):
    """These only read, so hitting whatever real store is ambient is harmless."""

    def setUp(self) -> None:
        self.client = TestClient(app.app)
        self._original_selection = runtime.selection
        runtime.selection = ModelSelection("mock", "static-demo")

    def tearDown(self) -> None:
        runtime.selection = self._original_selection

    def test_simple_catalog_endpoints_serialize(self) -> None:
        for path in ("/api/health", "/api/models", "/api/ocr", "/api/tts/status", "/api/question", "/api/library"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, f"{path} -> {response.text[:300]}")

    def test_lightweight_textbook_import_serializes(self) -> None:
        # import_textbook explicitly does not persist (stored: False) — safe to run in-process.
        response = self.client.post(
            "/api/textbook/import",
            files={"file": ("page.png", io.BytesIO(TINY_PNG), "image/png")},
            data={"sourceText": "解方程：2x + 3 = 11，求 x 的值。"},
        )
        self.assertEqual(response.status_code, 200, response.text[:500])
        body = response.json()
        self.assertFalse(body["stored"])
        self.assertIn("questionPayload", body)


class WritingEndpointResponseModelTests(unittest.TestCase):
    """These persist data, so each runs in its own process against an isolated store."""

    def test_full_pdf_pipeline_serializes(self) -> None:
        with TemporaryDirectory() as directory:
            output = run_isolated(
                textwrap.dedent(
                    """
                    import io, sys
                    import app
                    from fastapi.testclient import TestClient
                    from pypdf import PdfWriter

                    client = TestClient(app.app)
                    writer = PdfWriter()
                    writer.add_blank_page(width=200, height=200)
                    buffer = io.BytesIO()
                    writer.write(buffer)
                    pdf_bytes = buffer.getvalue()

                    init_response = client.post(
                        "/api/uploads/init",
                        json={
                            "filename": "test.pdf", "contentType": "application/pdf",
                            "size": len(pdf_bytes), "chunkSize": 1024, "totalChunks": 1, "sourceText": "",
                        },
                    )
                    assert init_response.status_code == 200, init_response.text
                    upload_id = init_response.json()["uploadId"]

                    chunk_response = client.put(f"/api/uploads/{upload_id}/chunks/0", content=pdf_bytes)
                    assert chunk_response.status_code == 200, chunk_response.text

                    status_response = client.get(f"/api/uploads/{upload_id}/status")
                    assert status_response.status_code == 200, status_response.text

                    complete_response = client.post(f"/api/uploads/{upload_id}/complete")
                    assert complete_response.status_code == 200, complete_response.text[:1500]
                    result = complete_response.json()

                    library_get = client.get(f"/api/library/{upload_id}")
                    assert library_get.status_code == 200, library_get.text[:1500]

                    library_list = client.get("/api/library")
                    assert library_list.status_code == 200
                    assert len(library_list.json()["items"]) == 1, library_list.json()

                    batch_id = result["batches"][0]["id"]
                    batch_response = client.post(f"/api/uploads/{upload_id}/batches/{batch_id}/process")
                    assert batch_response.status_code == 200, batch_response.text[:1500]
                    # Cache-hit path (force defaults to False) omits modelRuns/reviewRuns/reviewRun
                    # in the raw dict; response_model still serializes them as null, not an error.
                    assert batch_response.json().get("modelRuns") is None

                    print("OK")
                    """
                ),
                directory,
            )
            self.assertIn("OK", output)

    def test_learning_flow_serializes(self) -> None:
        with TemporaryDirectory() as directory:
            output = run_isolated(
                textwrap.dedent(
                    """
                    import app
                    from fastapi.testclient import TestClient

                    client = TestClient(app.app)
                    lesson_response = client.post(
                        "/api/lessons",
                        json={
                            "lessonId": "lesson-test-1", "title": "测试课程", "version": 1, "status": "draft",
                            "knowledgePoints": ["测试知识点"],
                            "blocks": [{"id": "b1", "type": "markdown", "title": "", "payload": {"markdown": "# hi"}}],
                        },
                    )
                    assert lesson_response.status_code == 200, lesson_response.text
                    assert "createdAt" in lesson_response.json()

                    get_response = client.get("/api/lessons/lesson-test-1")
                    assert get_response.status_code == 200, get_response.text

                    session_response = client.post(
                        "/api/learning/sessions",
                        json={"learnerId": "test-learner", "lessonId": "lesson-test-1"},
                    )
                    assert session_response.status_code == 200, session_response.text
                    session_id = session_response.json()["sessionId"]

                    attempt_response = client.post(
                        f"/api/learning/sessions/{session_id}/attempts",
                        json={
                            "questionId": "q1", "knowledgePoint": "测试知识点", "response": {"text": "x=4"},
                            "assessment": "correct", "hintLevel": 0, "durationMs": 1000,
                        },
                    )
                    assert attempt_response.status_code == 200, attempt_response.text
                    assert attempt_response.json()["mastery"]["knowledgePoint"] == "测试知识点"

                    mastery_response = client.get("/api/learning/mastery/test-learner")
                    assert mastery_response.status_code == 200
                    assert len(mastery_response.json()["items"]) == 1

                    print("OK")
                    """
                ),
                directory,
            )
            self.assertIn("OK", output)
