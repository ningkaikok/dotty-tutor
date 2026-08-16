from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from publication_quality import PublicationQualityError
from publication_routes import _public_lesson, build_publication_router


class PublicationBoundaryTests(unittest.TestCase):
    def test_student_projection_hides_studio_diagnostics(self) -> None:
        public = _public_lesson({
            "lessonId": "lesson-1",
            "title": "一次方程",
            "version": 1,
            "status": "published",
            "knowledgePoints": ["移项"],
            "blocks": [],
            "questionPayload": {
                "question": {
                    "id": "lesson-1",
                    "prompt": "解方程",
                    "publicationStatus": "ready",
                    "sourceArtifactUrl": "/private/source.md",
                    "promptArtifactUrl": "/private/prompt.md",
                },
                "lessonSteps": [],
                "architecture": {"source": "internal"},
                "review": {"status": "reviewed"},
                "quality": {"status": "ready"},
                "modelRun": {"provider": "codex", "model": "secret"},
            },
            "guideCards": [{"hint": "内部提示"}],
        })
        payload = public["questionPayload"]
        self.assertNotIn("review", payload)
        self.assertNotIn("quality", payload)
        self.assertNotIn("sourceArtifactUrl", payload["question"])
        self.assertNotIn("promptArtifactUrl", payload["question"])
        self.assertEqual(payload["modelRun"]["provider"], "published")
        self.assertEqual(public["guideCards"], [])

    def test_all_quarantined_questions_return_structured_diagnostics(self) -> None:
        class BlockedStore:
            def update_publication_status(self, _publication_id: str, _status: str) -> None:
                raise PublicationQualityError([{
                    "lessonId": "lesson-invalid",
                    "errors": ["题型结构不完整"],
                    "validatorVersion": "test-v1",
                }])

        app = FastAPI()
        app.include_router(build_publication_router(store=BlockedStore()))
        response = TestClient(app).patch(
            "/api/publications/paper-1/status",
            json={"status": "published"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "publication_quality_blocked")
        self.assertEqual(
            response.json()["detail"]["blockedLessons"][0]["lessonId"],
            "lesson-invalid",
        )

    def test_revision_endpoint_returns_new_version(self) -> None:
        class Store:
            pass

        class RevisionService:
            def create(self, publication_id: str) -> dict:
                return {
                    "publication": {
                        "publicationId": "paper-2",
                        "revisionOf": publication_id,
                        "version": 2,
                        "lessonIds": ["lesson-v2"],
                    },
                    "questionPayloads": [{"question": {"id": "lesson-v2"}}],
                    "run": {
                        "runId": "run-test", "operation": "publication_rereview", "scope": "publication",
                        "status": "succeeded", "config": {}, "startedAt": 1.0, "completedAt": 2.0,
                        "targetPublicationId": publication_id,
                    },
                }

        app = FastAPI()
        app.include_router(build_publication_router(
            store=Store(),
            revision_service=RevisionService(),
        ))
        response = TestClient(app).post("/api/publications/paper-1/revisions")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["publication"]["revisionOf"], "paper-1")
        self.assertEqual(response.json()["publication"]["version"], 2)

    def test_source_endpoint_restores_latest_studio_payloads(self) -> None:
        class Store:
            def list_publications(self, status=None) -> list[dict]:
                del status
                return [{
                    "publicationId": "paper-2",
                    "sourceUploadId": "upload-1",
                    "status": "in_review",
                }]

            def load_publication(self, _publication_id: str) -> dict:
                return {
                    "publicationId": "paper-2",
                    "sourceUploadId": "upload-1",
                    "status": "in_review",
                    "version": 2,
                    "lessonIds": ["lesson-v2"],
                    "lessons": [{
                        "lessonId": "lesson-v2",
                        "questionPayload": {"question": {"id": "lesson-v2"}},
                    }],
                }

        app = FastAPI()
        app.include_router(build_publication_router(store=Store()))
        response = TestClient(app).get("/api/publications/source/upload-1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["publication"]["version"], 2)
        self.assertEqual(response.json()["questionPayloads"][0]["question"]["id"], "lesson-v2")


if __name__ == "__main__":
    unittest.main()
