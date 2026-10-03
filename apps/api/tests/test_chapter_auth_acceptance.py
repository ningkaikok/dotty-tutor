"""Acceptance of chapter identities through the real protected middleware."""

from __future__ import annotations

from fastapi.testclient import TestClient

from application import create_app
from application.services.chapter_courses import ChapterCourseService
from persistence.app_store import AppStore
from persistence.auth_store import AuthSessionStore, auth_metadata
from routers.auth_routes import COOKIE_NAME
from routers.chapter_routes import build_chapter_router
from tests.auth_test_support import environment
from tests.postgres_test_support import PostgresTestCase


class ChapterAuthAcceptanceTests(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = AppStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.store.close)
        self.enterContext(environment({
            "AUTH_MODE": "protected",
            "AUTH_COOKIE_SECURE": "1",
            "TEACHER_BOOTSTRAP_SECRET": "chapter-acceptance-secret-" * 2,
            "CORS_ORIGINS": "https://testserver",
        }))
        auth_metadata.create_all(self.store.engine)
        auth_store = AuthSessionStore(engine=self.store.engine)
        app = create_app()
        app.state.auth_session_store = auth_store
        app.include_router(build_chapter_router(ChapterCourseService(self.store)))
        self.teacher = self._client(app, auth_store, "teacher", None)
        self.alpha = self._client(app, auth_store, "student", "chapter-alpha")
        self.beta = self._client(app, auth_store, "student", "chapter-beta")

    def _client(self, app, auth_store, role, learner_id):
        client = TestClient(app, base_url="https://testserver")
        token, _ = auth_store.create_session(role=role, learner_id=learner_id)
        client.cookies.set(COOKIE_NAME, token)
        self.addCleanup(client.close)
        return client

    def test_user_student_identity_is_bound_and_teacher_can_review_without_student_credentials(self) -> None:
        # Given a teacher-reviewed English publication and two protected student sessions
        created = self.teacher.post("/api/chapters", json={
            "subject": "english", "title": "Original reading",
            "source": {"license": "Original synthetic content", "pages": [
                {"page": 1, "text": "Mina moved to Boston. She enjoys painting."},
            ]},
        })
        self.assertEqual(created.status_code, 201, created.text)
        chapter_id = created.json()["chapterId"]
        root = f"/api/chapters/{chapter_id}"
        generated = self.teacher.post(f"{root}/generate")
        self.assertEqual(generated.status_code, 200, generated.text)
        chapter = generated.json()
        lesson = chapter["lessons"][0]
        revision = chapter["sourceRevisions"][-1]
        sentence = revision["pages"][0]["sentences"][0]
        reference = {"sourceRevisionId": revision["sourceRevisionId"], "page": 1,
                     "sentenceId": sentence["sentenceId"], "quote": sentence["text"]}
        edited = self.teacher.put(f"{root}/lessons/{lesson['lessonId']}", json={
            "prompt": "Where did Mina move?", "answer": "Boston", "answerType": "text",
            "questionKind": "explicit", "answerMode": "short_answer", "acceptedAnswers": ["Boston"],
            "requiredEvidenceRefs": [reference], "rubric": {"supportStatus": "supported"},
            "sourceRevisionId": revision["sourceRevisionId"], "page": 1,
            "expectedRecordVersion": chapter["recordVersion"],
        })
        self.assertEqual(edited.status_code, 200, edited.text)
        review = self.teacher.patch(f"{root}/lessons/{lesson['lessonId']}/review", json={
            "decision": "approve", "reviewer": "acceptance-teacher",
            "expectedRecordVersion": edited.json()["recordVersion"],
        })
        self.assertEqual(review.status_code, 200, review.text)
        published = self.teacher.post(f"{root}/publish")
        self.assertEqual(published.status_code, 201, published.text)
        self.assertEqual(self.teacher.get(f"{root}/published").status_code, 200)

        # When a student submits an unlisted paraphrase with valid evidence and omits learnerId
        public = self.alpha.get(f"{root}/published")
        self.assertEqual(public.status_code, 200, public.text)
        self.assertNotIn("acceptedAnswers", public.text)
        submission = {
            "attemptId": "protected-chapter-attempt", "lessonId": lesson["lessonId"],
            "questionId": lesson["lessonId"], "publicationId": published.json()["publicationId"],
            "answer": {"text": "She relocated to Boston."}, "evidenceRefs": [reference],
        }
        response = self.alpha.post(f"{root}/attempts", json=submission)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["learnerId"], "chapter-alpha")
        self.assertEqual(response.json()["assessment"], "needs_review")

        # Then only its owner can restore it, neither student may edit/review, and a teacher can decide it
        attempt_path = f"{root}/attempts/{submission['attemptId']}"
        self.assertEqual(self.alpha.get(attempt_path).status_code, 200)
        self.assertEqual(self.beta.get(attempt_path).status_code, 404)
        self.assertEqual(self.alpha.get(attempt_path, params={"learnerId": "chapter-beta"}).status_code, 403)
        self.assertEqual(self.alpha.post(f"{root}/attempts", json={**submission, "learnerId": "chapter-beta"}).status_code, 403)
        for client in (self.alpha, self.beta):
            self.assertEqual(client.get(root).status_code, 403)
            self.assertEqual(client.get(f"{root}/review-attempts/{submission['attemptId']}").status_code, 403)
            self.assertEqual(client.post(f"{root}/generate").status_code, 403)
            self.assertEqual(client.post(f"{root}/publish").status_code, 403)
            self.assertEqual(client.patch(f"{attempt_path}/review", json={
                "reviewer": "forged", "decision": "correct", "note": ""}).status_code, 403)
        self.assertEqual(self.teacher.get(attempt_path).status_code, 403)
        teacher_read = self.teacher.get(f"{root}/review-attempts/{submission['attemptId']}")
        self.assertEqual(teacher_read.status_code, 200, teacher_read.text)
        decision = self.teacher.patch(f"{attempt_path}/review", json={
            "reviewer": "acceptance-teacher", "decision": "correct", "note": "原文支持此改写",
        })
        self.assertEqual(decision.status_code, 200, decision.text)
        restored = self.alpha.get(attempt_path)
        self.assertEqual(restored.json()["assessment"], "correct")
        original = self.store.get_chapter_attempt(submission["attemptId"])
        self.assertIsNotNone(original)
        assert original is not None
        self.assertEqual(original["assessment"], "needs_review")
        self.assertEqual(original["answer"], submission["answer"])
        self.assertEqual(len(original["reviews"]), 1)
        self.assertEqual(self.store.list_mastery("chapter-alpha"), [])

    def test_user_imports_malformed_page_flags_then_validation_rejects_without_creating_a_chapter(self) -> None:
        # Given a teacher supplying incomplete page problem metadata
        body = {
            "subject": "math", "title": "Invalid source",
            "source": {"license": "Original synthetic content", "pages": [
                {"page": 1, "text": "y=2x+1"},
            ], "pageFlags": [{"flags": ["unreadable"]}]},
        }
        # When the typed import boundary validates it
        response = self.teacher.post("/api/chapters", json=body)
        # Then the error is a client validation response and there is no partial chapter
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.store.list_chapters(), [])
