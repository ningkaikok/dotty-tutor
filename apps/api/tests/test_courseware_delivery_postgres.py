"""Acceptance for real persistence and source/role boundaries of courseware adapters."""
from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from application import create_app
from application.job_worker import JobWorker
from application.lesson_media_jobs import build_lesson_media_registry
from application.services.chapter_courses import ChapterCourseService
from application.services.courseware import CoursewareService
from application.services.lesson_media import LessonMediaService
from application.services.voice_tutor import VoiceTutorService
from infrastructure.runtime.model_runtime import ModelRuntime
from infrastructure.runtime.voice_transcription import VoiceTranscriptionRuntime
from persistence.app_store import AppStore
from persistence.auth_store import AuthSessionStore
from persistence.courseware_store import CoursewareStore
from persistence.job_store import JobStore
from persistence.lesson_media_store import LessonMediaStore
from persistence.voice_store import VoiceStore
from routers.auth_routes import COOKIE_NAME
from routers.courseware_routes import build_courseware_router
from tests.auth_test_support import environment
from tests.postgres_test_support import PostgresTestCase
from tests.test_lesson_media_postgres import SpeechRuntimeFixture


class CoursewareDeliveryTests(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.app_store = AppStore(database_url=self.database_url, data_root=self.data_root)
        self.jobs = JobStore(database_url=self.database_url, data_root=self.data_root)
        self.media_store = LessonMediaStore(database_url=self.database_url, data_root=self.data_root)
        self.voice_store = VoiceStore(database_url=self.database_url, data_root=self.data_root)
        for store in (self.app_store, self.jobs, self.media_store, self.voice_store):
            self.addCleanup(store.close)
        self.chapters = ChapterCourseService(self.app_store, self.jobs)
        self.courseware_store = CoursewareStore(engine=self.app_store.engine)
        self.courseware = CoursewareService(store=self.courseware_store, chapters=self.chapters, jobs=self.jobs)
        self.media = LessonMediaService(chapter_service=self.chapters, courseware_service=self.courseware,
                                      media_store=self.media_store, jobs=self.jobs, speech_runtime=SpeechRuntimeFixture(duration_seconds=20))
        self.voice = VoiceTutorService(store=self.voice_store, chapter_service=self.chapters,
            courseware_service=self.courseware, tutor_runtime=ModelRuntime(env_prefix="TUTOR_"), stt_runtime=VoiceTranscriptionRuntime())
        self.chapter = self.chapters.create({"title": "Engineering science fixture", "subject": "science", "teachingMode": "tutorial",
            "source": {"license": "CC BY 4.0", "attributionText": "Engineering fixture, not teacher acceptance",
                       "distributionScope": "classroom", "pageStart": 1, "pageEnd": 4,
                       "pages": [{"page": number, "text": "Observe the water cycle and compare the evidence."} for number in range(1, 5)]}})
        self.source = self.chapter["sourceRevisions"][-1]
        self.cid, self.pid = "courseware-delivery", "publication-delivery"
        self.pages = [{"pageId": f"page-{n}", "title": "Observe the evidence", "sourceRefs": [{"sourceRevisionId": self.source["sourceRevisionId"], "page": n, "sentenceIds": [self.source["pages"][n-1]["sentences"][0]["sentenceId"]]}],
                       "generationStatus": "completed", "reviewStatus": "approved", "teacherNotes": "PRIVATE_TEACHER",
                       "blocks": [{"blockId": f"teach-{n}", "kind": "text", "text": "Observe the water cycle and compare the evidence."},
                                  {"blockId": f"activity-{n}", "kind": "activity", "answerKey": "PRIVATE_ANSWER"}]} for n in range(1, 4)]
        self.courseware_store.save_record(record_id=f"document:{self.cid}", record_type="document", courseware_id=self.cid,
            chapter_id=self.chapter["chapterId"], source_revision_id=self.source["sourceRevisionId"], status="published", expected_version=0,
            payload={"coursewareId": self.cid, "chapterId": self.chapter["chapterId"], "sourceRevisionId": self.source["sourceRevisionId"], "pages": self.pages})
        self.courseware_store.save_publication({"publicationId": self.pid, "coursewareId": self.cid, "version": 1,
            "sourceRevisionId": self.source["sourceRevisionId"], "sourceTitle": "Engineering fixture", "sourceUrl": None,
            "attributionText": "Fixture", "distributionScope": "classroom", "publishedAt": 1_800_000_000,
            "reviewRecords": [], "snapshot": {"chapterId": self.chapter["chapterId"], "title": "Observe", "subject": "science",
                                               "sourceLicense": "CC BY 4.0", "pages": self.pages}})

    def _request(self) -> dict[str, Any]:
        return {"sourceKind": "courseware", "coursewareId": self.cid, "publicationId": self.pid,
                "sourceRevisionId": self.source["sourceRevisionId"], "pageIds": [page["pageId"] for page in self.pages]}

    def test_user_renders_reviews_and_publishes_courseware_video_then_only_matching_publication_can_deliver_it(self) -> None:
        # Given a source-bound publication with private teacher facilitation and assessment answers
        draft = self.media.create_draft(self._request())
        self.assertNotIn("PRIVATE_", str(draft["scenes"]))
        self.assertEqual(draft["publicationId"], self.pid)
        self.assertEqual(draft["sourceSnapshot"]["sourceKind"], "courseware")
        self.assertEqual(self.media.list_for_courseware(self.cid, self.pid, public=True)["items"], [])
        with self.assertRaises(ValueError):
            self.media.publish(draft["mediaId"], actor={"actorId": "fixture"})
        # When the real renderer processes fixture audio, and explicit test reviews approve this engineering artifact
        self.media.enqueue_render(draft["mediaId"], expected_version=1)
        worker = JobWorker(self.jobs, build_lesson_media_registry(self.media), worker_id="courseware-adapter-test")
        result = worker.run_once()
        self.assertEqual(result["status"], "succeeded", result)
        rendered = self.media.get(draft["mediaId"])
        self.assertEqual(rendered["rendererSnapshot"]["tts"][0]["language"], "Chinese")
        self.assertEqual(rendered["rendererSnapshot"]["tts"][0]["speaker"], "Serena")
        actor = {"actorId": "engineering-fixture", "actorKind": "demo_teacher", "identityVerified": False}
        self.media.review(draft["mediaId"], {"decision": "approve", "note": "Synthetic engineering evidence only"}, actor=actor)
        self.media.publish(draft["mediaId"], actor=actor)
        # Then the public projection contains only the pinned artifact and captions; chapter URLs reject it
        items = self.media.list_for_courseware(self.cid, self.pid, public=True)["items"]
        self.assertEqual(len(items), 1)
        self.assertTrue(self.media.courseware_asset(self.cid, self.pid, draft["mediaId"], kind="asset").is_file())
        with self.assertRaises(LookupError):
            self.media.courseware_asset("wrong-courseware", self.pid, draft["mediaId"], kind="asset")
        with self.assertRaises(LookupError):
            self.media.public_asset(self.chapter["chapterId"], draft["mediaId"], kind="asset")

    def test_user_requests_private_assessment_voice_or_forged_source_then_the_adapter_refuses(self) -> None:
        # Given a published teaching page, When selecting activity answers or another source, Then fail closed.
        values = {"learnerId": "test-learner", "publicationId": self.pid, "pageId": "page-1", "blockId": "activity-1"}
        with self.assertRaises(LookupError):
            self.voice.create_courseware_session(self.cid, values)
        values["blockId"] = "teach-1"
        session = self.voice.create_courseware_session(self.cid, values)
        restored = self.voice.get_session(session["sessionId"], session["recoveryToken"], learner_id="test-learner")
        self.assertEqual(restored["sourceKind"], "courseware")
        self.assertEqual(restored["coursewareId"], self.cid)
        self.assertNotIn("PRIVATE_", str(self.voice._session_context(restored)))
        forged = {**self._request(), "sourceRevisionId": "forged"}
        with self.assertRaisesRegex(ValueError, "修订"):
            self.media.create_draft(forged)
        changed = copy.deepcopy(self._request())
        changed["pageIds"] = ["page-1", "page-2", "missing"]
        with self.assertRaises(LookupError):
            self.media.create_draft(changed)

    def test_user_opens_unit_coverage_then_unreviewed_source_page_is_still_visible_as_a_gap(self) -> None:
        # Given only pages 1–3 generated for a four-page source, When reading coverage, Then page 4 remains missing.
        report = self.courseware.get_coverage(self.chapter["chapterId"])
        self.assertEqual(report["ungeneratedPages"], [4])
        self.assertEqual(report["unreviewedPages"], [4])
        self.assertFalse(report["complete"])
        self.assertEqual(report["qualityAcceptance"], "requires_teacher_validation")

    def test_user_withdraws_source_during_voice_generation_then_no_reply_is_saved_and_response_slot_is_released(self) -> None:
        # Given a learner has confirmed a transcript on a published teaching block
        learner_id = "withdrawal-learner"
        session = self.voice.create_courseware_session(self.cid, {
            "learnerId": learner_id, "publicationId": self.pid,
            "pageId": "page-1", "blockId": "teach-1",
        })
        session_id, token = session["sessionId"], session["recoveryToken"]
        draft = self.voice.save_manual_transcript(
            session_id, token, learner_id=learner_id, text="Explain this step.",
        )
        revision = draft["draftTranscript"]["revision"]
        self.voice.confirm_transcript(
            session_id, token, learner_id=learner_id,
            revision=revision, text="Explain this step.",
        )
        chapters, chapter_id = self.chapters, self.chapter["chapterId"]

        class SourceWithdrawalRuntime:
            """Model-boundary fixture that withdraws the source before returning."""

            selection = SimpleNamespace(provider="acceptance-runtime")

            def generate_json(self, *_args: Any, **_kwargs: Any) -> tuple[dict[str, str], dict[str, Any]]:
                chapters.delete(chapter_id)
                return {"reply": "This stale reply must never be saved."}, {}

        self.voice.tutor_runtime = SourceWithdrawalRuntime()
        # When the teacher withdraws the source while the model is producing a reply
        with self.assertRaisesRegex(LookupError, "来源章节不存在"):
            self.voice.respond(
                session_id, token, learner_id=learner_id, turn_id="withdrawn-turn",
                transcript_revision=revision, expected_generation=draft["generation"],
            )
        # Then the reply is rejected, the confirmed transcript survives, and no slot remains active
        restored = self.voice.get_session(session_id, token, learner_id=learner_id)
        self.assertEqual(restored["turns"], [])
        self.assertFalse(restored["active"])
        self.assertEqual(restored["generation"], draft["generation"] + 1)
        self.assertEqual(restored["confirmedTranscript"]["text"], "Explain this step.")

    def test_user_student_uses_real_auth_then_teacher_mutations_and_foreign_identity_are_denied(self) -> None:
        # Given real protected middleware and a durable student session
        self.enterContext(environment({"AUTH_MODE": "protected", "AUTH_COOKIE_SECURE": "1", "TEACHER_BOOTSTRAP_SECRET": "engineering-test-secret-" * 2, "CORS_ORIGINS": "https://testserver"}))
        auth = AuthSessionStore(engine=self.app_store.engine)
        app = create_app()
        app.state.auth_session_store = auth
        app.include_router(build_courseware_router(self.courseware, media_service=self.media, voice_service=self.voice))
        client = self.enterContext(TestClient(app, base_url="https://testserver"))
        token, _ = auth.create_session(role="student", learner_id="bound-student")
        client.cookies.set(COOKIE_NAME, token)
        root = f"/api/courseware/{self.cid}"
        # When requesting teacher authoring or spoofing the learner header
        self.assertEqual(client.get(f"/api/chapters/{self.chapter['chapterId']}/courseware-coverage").status_code, 403)
        self.assertEqual(client.post(f"{root}/media", json={}).status_code, 403)
        values = {"publicationId": self.pid, "pageId": "page-1", "blockId": "teach-1", "learnerId": "forged-body"}
        self.assertEqual(client.post(f"{root}/voice-sessions", json=values, headers={"X-Learner-ID": "wrong-header"}).status_code, 403)
        self.assertEqual(client.post(f"{root}/voice-sessions", json=values, headers={"X-Learner-ID": "bound-student"}).status_code, 403)
        values["learnerId"] = "bound-student"
        result = client.post(f"{root}/voice-sessions", json=values, headers={"X-Learner-ID": "bound-student"})
        self.assertEqual(result.status_code, 201, result.text)
        self.assertEqual(result.json()["learnerId"], "bound-student")
        self.assertEqual(client.get(f"{root}/media/published?publicationId={self.pid}").status_code, 200)
