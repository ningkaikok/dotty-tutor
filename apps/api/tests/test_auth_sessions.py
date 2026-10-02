from __future__ import annotations

import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from persistence.auth_store import AuthSessionStore, auth_metadata, auth_sessions
from routers.auth_routes import build_auth_router
from tests.auth_test_support import environment


class AuthSessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        auth_metadata.create_all(self.engine)
        self.store = AuthSessionStore(engine=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_session_token_is_opaque_revocable_and_invitation_is_single_use(self) -> None:
        teacher_token, teacher = self.store.create_session(role="teacher", learner_id=None)
        invite = self.store.create_invite(learner_id="student-a", teacher_session_id=teacher["sessionId"])
        student_result = self.store.consume_invite(invite)
        self.assertIsNotNone(student_result)
        assert student_result is not None
        student_token, student = student_result
        self.assertEqual(self.store.resolve(student_token)["learnerId"], "student-a")
        self.assertIsNone(self.store.consume_invite(invite))
        with self.engine.connect() as connection:
            saved_hash = connection.execute(select(auth_sessions.c.token_hash).where(
                auth_sessions.c.session_id == student["sessionId"]
            )).scalar_one()
        self.assertNotEqual(saved_hash, student_token)
        self.assertTrue(self.store.revoke(student["sessionId"]))
        self.assertIsNone(self.store.resolve(student_token))
        self.assertIsNotNone(self.store.resolve(teacher_token))


class ProtectedModeRouteTests(unittest.TestCase):
    def test_anonymous_request_is_rejected_and_roles_are_separated(self) -> None:
        from application import create_app
        from routers.prompt_routes import build_prompt_router

        class _Jobs:
            records = {
                "own": {"jobId": "own", "jobType": "mistake.image.import", "payload": {"learnerId": "student-a"}},
                "other": {"jobId": "other", "jobType": "mistake.image.import", "payload": {"learnerId": "student-b"}},
                "textbook": {"jobId": "textbook", "jobType": "textbook.upload.complete", "payload": {"learnerId": "student-a"}},
            }

            def get_job(self, job_id):
                return self.records.get(job_id)

        engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        auth_metadata.create_all(engine)
        store = AuthSessionStore(engine=engine)
        env = {
            "AUTH_MODE": "protected",
            "AUTH_COOKIE_SECURE": "1",
            "TEACHER_BOOTSTRAP_SECRET": "a" * 40,
            "CORS_ORIGINS": "https://testserver",
            "DOTTY_CONTENT_EDITOR_TOKEN": "editor-token",
        }
        with environment(env):
            app = create_app()
            app.state.auth_session_store = store
            app.state.background_job_store = _Jobs()
            app.include_router(build_auth_router(session_store=store))
            app.include_router(build_prompt_router(store=object()))
            app.add_api_route("/api/classes", lambda: {"items": []}, methods=["GET"])
            app.add_api_route("/api/assignments", lambda: {"items": []}, methods=["GET"])
            app.add_api_route("/api/funnel", lambda: {"items": []}, methods=["GET"])
            app.add_api_route("/api/tts", lambda: {"accepted": True}, methods=["POST"])
            app.add_api_route("/api/tts/status", lambda: {"available": True}, methods=["GET"])
            app.add_api_route("/api/jobs/{job_id}", lambda job_id: {"jobId": job_id}, methods=["GET"])
            app.add_api_route("/api/jobs/{job_id}/cancel", lambda job_id: {"jobId": job_id}, methods=["POST"])
            app.add_api_route("/api/jobs/{job_id}/retry", lambda job_id: {"jobId": job_id}, methods=["POST"])
            with TestClient(app, base_url="https://testserver") as client:
                self.assertEqual(
                    client.get("/api/content/prompts/not-a-template", headers={"X-Content-Token": "editor-token"}).status_code,
                    401,
                )
                self.assertEqual(client.get("/api/classes").status_code, 401)
                login = client.post("/api/auth/sessions", json={"teacherSecret": "a" * 40})
                self.assertEqual(login.status_code, 200)
                self.assertEqual(client.get("/api/classes").status_code, 200)
                client.delete("/api/auth/sessions")
                teacher_login = client.post("/api/auth/sessions", json={"teacherSecret": "a" * 40})
                self.assertEqual(teacher_login.status_code, 200)
                self.assertEqual(
                    client.get("/api/content/prompts/not-a-template", headers={"X-Content-Token": "editor-token"}).status_code,
                    404,
                )
                invite = client.post("/api/auth/invites", json={"learnerId": "student-a"})
                self.assertEqual(invite.status_code, 200)
                client.delete("/api/auth/sessions")
                student_login = client.post("/api/auth/sessions", json={"inviteToken": invite.json()["inviteToken"]})
                self.assertEqual(student_login.status_code, 200)
                self.assertEqual(student_login.json()["learnerId"], "student-a")
                self.assertEqual(client.get("/api/auth/sessions").status_code, 200)
                self.assertEqual(client.get("/api/classes").status_code, 403)
                self.assertEqual(client.get("/api/system/dependency-preflight").status_code, 403)
                self.assertEqual(client.get("/api/tts/status").status_code, 403)
                self.assertEqual(client.get("/api/debug/errors").status_code, 403)
                self.assertEqual(client.get("/api/publications/source/upload-a").status_code, 403)
                self.assertEqual(client.get("/api/assignments").status_code, 200)
                self.assertEqual(client.get("/api/funnel").status_code, 200)
                self.assertEqual(client.post("/api/tts", json={"text": "请读这段提示"}).status_code, 200)
                self.assertEqual(client.get("/api/tts/status").status_code, 403)
                self.assertEqual(client.post("/api/tts", json={"text": "无效来源"}, headers={"Origin": "https://evil.example"}).status_code, 403)
                self.assertEqual(client.get("/api/content/prompts/not-a-template", headers={"X-Content-Token": "editor-token"}).status_code, 403)
                self.assertEqual(client.get("/api/jobs/own").status_code, 200)
                self.assertEqual(client.post("/api/jobs/own/cancel").status_code, 200)
                self.assertEqual(client.post("/api/jobs/own/retry").status_code, 200)
                self.assertEqual(client.get("/api/jobs/other").status_code, 404)
                self.assertEqual(client.get("/api/jobs/textbook").status_code, 404)
                self.assertEqual(
                    client.post("/api/assignments", headers={"Origin": "https://evil.example"}).status_code,
                    403,
                )
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
