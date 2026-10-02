from __future__ import annotations

import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from application import create_app
from persistence.auth_store import AuthSessionStore, auth_metadata
from routers.auth_routes import build_auth_router
from routers.learning_routes import build_learning_router
from routers.mistake_routes import build_mistake_router
from routers.practice_routes import build_practice_router
from routers.review_routes import build_review_router
from routers.tutor_input_routes import build_tutor_input_router
from routers.tutoring_routes import build_tutoring_router
from tests.auth_test_support import environment


class _Mistakes:
    def get(self, _key: str):
        return {"mistakeId": "mistake-b", "learnerId": "student-b", "status": "unmastered"}

    def list(self, _learner: str, **_kwargs: object):
        return []

    def item_directory(self, _key: str) -> Path:
        return Path("/tmp")

    @property
    def mistake_root(self) -> Path:
        return Path("/tmp")


class _Tutoring:
    def get(self, _key: str, **_kwargs: object):
        return {"threadId": "thread-b", "mistakeId": "mistake-b", "learnerId": "student-b", "stage": "diagnose"}

    def get_input(self, _key: str):
        return {"inputId": "input-b", "threadId": "thread-b", "mistakeId": "mistake-b", "learnerId": "student-b", "status": "confirmed"}

    def get_artifact(self, _key: str):
        return {"artifactId": "artifact-b", "inputId": "input-b", "learnerId": "student-b", "storagePath": "/tmp/not-read", "mediaType": "image/png", "filename": "x.png"}

    def list_tool_events(self, _key: str):
        return []


class _Learning:
    def get_learning_session(self, _key: str):
        return {"sessionId": "session-b", "learnerId": "student-b"}


class _Reviews:
    def get(self, _key: str):
        return {"taskId": "review-b", "learnerId": "student-b", "status": "scheduled", "mistakeId": "mistake-b"}


class _Variations:
    def get(self, _key: str):
        return {"variationId": "variation-b", "learnerId": "student-b", "mistakeId": "mistake-b", "status": "ready"}


class ProtectedOwnershipTests(unittest.TestCase):
    def test_student_cannot_read_another_learners_resources(self) -> None:
        engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        auth_metadata.create_all(engine)
        sessions = AuthSessionStore(engine=engine)
        env = {
            "AUTH_MODE": "protected", "AUTH_COOKIE_SECURE": "1",
            "TEACHER_BOOTSTRAP_SECRET": "b" * 40, "CORS_ORIGINS": "https://testserver",
        }
        with environment(env):
            app = create_app()
            app.state.auth_session_store = sessions
            app.include_router(build_auth_router(session_store=sessions))
            mistakes, tutoring, reviews, variations = _Mistakes(), _Tutoring(), _Reviews(), _Variations()
            app.include_router(build_mistake_router(store=mistakes, recognize=lambda *_: ({}, [], {}, {})))
            app.include_router(build_tutoring_router(mistake_store=mistakes, tutoring_store=tutoring, tutor=object()))
            app.include_router(build_tutor_input_router(tutoring_store=tutoring, input_service=object(), mistake_store=mistakes))
            app.include_router(build_learning_router(store=_Learning()))
            app.include_router(build_review_router(mistake_store=mistakes, review_store=reviews, variation_service=object()))
            app.include_router(build_practice_router(
                mistake_store=mistakes, tutoring_store=tutoring, variation_store=variations,
                variation_service=object(), review_store=reviews,
            ))
            with TestClient(app, base_url="https://testserver") as client:
                client.post("/api/auth/sessions", json={"teacherSecret": "b" * 40})
                invitation = client.post("/api/auth/invites", json={"learnerId": "student-a"}).json()["inviteToken"]
                client.delete("/api/auth/sessions")
                self.assertEqual(client.post("/api/auth/sessions", json={"inviteToken": invitation}).status_code, 200)

                requests = [
                    client.get("/api/mistakes/mistake-b"),
                    client.get("/api/tutor/threads/thread-b"),
                    client.get("/api/tutor/inputs/input-b/artifacts/artifact-b"),
                    client.get("/api/learning/sessions/session-b"),
                    client.post("/api/reviews/review-b/start"),
                    client.post("/api/variations/variation-b/answer", json={"content": "A"}),
                    client.get("/api/mistakes?learnerId=student-b"),
                ]
                self.assertEqual([response.status_code for response in requests], [404, 404, 404, 404, 404, 404, 403])
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
