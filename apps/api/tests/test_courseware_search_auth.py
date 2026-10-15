"""Published textbook retrieval through real sessions, middleware, stores, and rendering."""
from __future__ import annotations

from fastapi.testclient import TestClient

from application import create_app
from application.services.chapter_courses import ChapterCourseService
from persistence.app_store import AppStore
from persistence.auth_store import AuthSessionStore
from persistence.courseware_store import CoursewareStore
from persistence.job_store import JobStore
from routers.auth_routes import COOKIE_NAME
from routers.courseware_search_routes import build_courseware_search_router
from tests import test_courseware_search_postgres as evidence_acceptance
from tests.auth_test_support import environment
from tests.postgres_test_support import PostgresTestCase


class CoursewareSearchAuthAcceptanceTests(PostgresTestCase):
    _source_course = evidence_acceptance.CoursewareSearchAcceptanceTests._source_course
    _search_fixture = evidence_acceptance.CoursewareSearchAcceptanceTests._search_fixture

    def setUp(self) -> None:
        super().setUp()
        self.app_store = AppStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.app_store.close)
        self.jobs = JobStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.jobs.close)
        self.courseware_store = CoursewareStore(engine=self.app_store.engine)
        self.chapters = ChapterCourseService(self.app_store, self.jobs)

    def test_user_student_reads_published_evidence_but_cannot_escape_its_publication_or_open_authoring(self) -> None:
        # Given a real reviewed publication and a protected learner session
        service, _index, chapter, _source, cid, pid = self._search_fixture()
        self.enterContext(environment({"AUTH_MODE": "protected", "AUTH_COOKIE_SECURE": "1",
                                       "TEACHER_BOOTSTRAP_SECRET": "evidence-acceptance-secret-" * 2,
                                       "CORS_ORIGINS": "https://testserver"}))
        sessions = AuthSessionStore(engine=self.app_store.engine)
        app = create_app()
        app.state.auth_session_store = sessions
        app.include_router(build_courseware_search_router(service))
        client = self.enterContext(TestClient(app, base_url="https://testserver"))
        token, _ = sessions.create_session(role="student", learner_id="evidence-learner")
        client.cookies.set(COOKIE_NAME, token)
        path = f"/api/courseware/{cid}/source-search"
        # When the learner searches, asks for cited explanation, and opens the original page
        result = client.get(path, params={"publicationId": pid, "q": "cited"})
        self.assertEqual(result.status_code, 200, result.text)
        item = result.json()["items"][0]
        answer = client.post(f"{path}/explain", json={"publicationId": pid, "question": "cited"})
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(answer.json()["status"], "answered")
        original = client.get(item["previewUrl"])
        self.assertEqual(original.status_code, 200, original.text[:200] if original.status_code != 200 else "")
        self.assertTrue(original.content.startswith(b"\x89PNG"))
        self.assertIn("no-store", original.headers["cache-control"])
        # Then exact student routes work, while authoring, foreign publications, and stale evidence do not
        self.assertEqual(client.get(f"/api/courseware/{cid}/presentation").status_code, 403)
        self.assertEqual(client.get(f"/api/chapters/{chapter['chapterId']}/sources/private/pages/24/preview").status_code, 403)
        self.assertEqual(client.get("/api/courseware/foreign/source-search", params={"publicationId": pid, "q": "cited"}).status_code, 404)
        chapter["deletedAt"] = 123
        self.app_store.save_chapter(chapter)
        self.assertEqual(client.get(item["previewUrl"]).status_code, 404)
        self.assertEqual(client.get(path, params={"publicationId": pid, "q": "cited"}).status_code, 404)
        client.cookies.clear()
        self.assertEqual(client.get(path, params={"publicationId": pid, "q": "cited"}).status_code, 401)
