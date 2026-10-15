"""User scenarios for unit navigation, immutable versions, and learner isolation."""

import copy
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from application import create_app
from application.courseware_jobs import build_courseware_registry
from application.job_worker import JobWorker
from application.services.chapter_courses import ChapterCourseService
from application.services.courseware_unit import CoursewareUnitService
from domain.contracts.courseware import CoursewareAttemptRequest, CoursewarePlanCreate
from domain.contracts.courseware_unit import CoursewarePositionSave
from persistence.app_store import AppStore
from persistence.auth_store import AuthSessionStore
from persistence.courseware_store import CoursewareStore, courseware_attempts
from persistence.courseware_unit_store import CoursewareUnitStore
from persistence.job_store import JobStore
from routers.auth_routes import COOKIE_NAME
from routers.courseware_unit_routes import build_courseware_unit_router
from tests import test_courseware_postgres_acceptance as fixtures
from tests.auth_test_support import environment
from tests.postgres_test_support import PostgresTestCase


class CoursewareUnitPostgresTests(PostgresTestCase):
    _source_course = fixtures.CoursewarePostgresAcceptanceTests._source_course

    def setUp(self) -> None:
        super().setUp()
        self.app_store = AppStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.app_store.close)
        self.jobs = JobStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.jobs.close)
        self.courseware_store = CoursewareStore(engine=self.app_store.engine)
        self.chapters = ChapterCourseService(self.app_store, self.jobs)

    def _published_unit(self) -> tuple[Any, Any, Any, Any]:
        chapter, _source, _refs, service = self._source_course()
        service.runtime.fail_second_page_once = False
        service.create_plan(chapter["chapterId"], CoursewarePlanCreate(
            subject="english", audience="Readers", languagePolicy="English", durationMinutes=10,
            objectives=["Read cited evidence"], expectedRecordVersion=chapter["recordVersion"],
            pageStart=23, pageEnd=24, autoGeneratePages=True,
        ))
        worker = JobWorker(self.jobs, build_courseware_registry(service), worker_id="unit-navigation")
        self.assertEqual(worker.run_once()["status"], "succeeded")
        self.assertEqual(worker.run_once()["status"], "succeeded")
        document = service.get_document(service.list_chapter_documents(chapter["chapterId"])["items"][0]["coursewareId"])
        publication = service.publish(document["coursewareId"], expected_record_version=document["recordVersion"],
                                      confirm_review=True, actor_id="unit-teacher")
        units = CoursewareUnitService(courseware=service, store=CoursewareUnitStore(engine=self.app_store.engine))
        return chapter, service, publication, units

    def test_user_opens_unit_with_unpublished_lesson_then_only_accessible_lessons_are_exposed(self) -> None:
        # Given a unit with one published lesson and another pending draft
        chapter, service, publication, units = self._published_unit()
        draft = copy.deepcopy(service.store.load_document(publication["coursewareId"]))
        draft.update(coursewareId="pending-session", title="Pending teacher lesson", publicationId=None, status="draft")
        service.store.save_record(record_id="document:pending-session", record_type="document", chapter_id=chapter["chapterId"],
                                  courseware_id="pending-session", source_revision_id=draft["sourceRevisionId"],
                                  payload=draft, status="draft", expected_version=0)
        plan = service.store.load_plan(chapter["chapterId"])
        plan["coursewareIds"] = ["pending-session", publication["coursewareId"]]
        service.store.save_record(record_id=f"plan:{chapter['chapterId']}", record_type="plan", chapter_id=chapter["chapterId"],
                                  courseware_id=None, source_revision_id=draft["sourceRevisionId"], payload=plan,
                                  status=plan["status"], expected_version=plan["recordVersion"])
        # When a student opens the directory, Then only the frozen published lesson is listed without answer content
        public = units.directory(chapter["chapterId"], learner_id="student-a")
        self.assertEqual([lesson["coursewareId"] for lesson in public["lessons"]], [publication["coursewareId"]])
        self.assertNotIn("pages", public["lessons"][0])
        self.assertEqual([lesson["coursewareId"] for lesson in units.directory(chapter["chapterId"], teacher=True)["lessons"]],
                         ["pending-session", publication["coursewareId"]])
        # A new source revision does not accidentally put the former batch back into the student's current unit.
        chapter["sourceRevisions"].append({**chapter["sourceRevisions"][-1], "sourceRevisionId": "replacement-source"})
        self.app_store.save_chapter(chapter)
        self.assertEqual(units.directory(chapter["chapterId"], learner_id="student-a")["lessons"], [])
        self.assertFalse(units.directory(chapter["chapterId"], teacher=True)["lessons"][0]["currentBatch"])

    def test_user_returns_from_another_device_then_own_original_publication_and_page_are_restored(self) -> None:
        # Given a learner has opened the second page of a frozen lesson
        chapter, service, publication, units = self._published_unit()
        courseware_id = publication["coursewareId"]
        request = CoursewarePositionSave(publicationId=publication["publicationId"], pageId="page-response",
                                         expectedVersion=0, commandId="open-second-page")
        first = units.save_position(courseware_id, learner_id="student-a", request=request, now=100)
        # When the same request is retried, Then it is idempotent and another learner has no reading history
        repeated = units.save_position(courseware_id, learner_id="student-a", request=request, now=200)
        self.assertEqual(first, repeated)
        self.assertIsNone(units.directory(chapter["chapterId"], learner_id="student-b")["resume"])
        resume = units.directory(chapter["chapterId"], learner_id="student-a")["resume"]
        self.assertEqual((resume["publicationId"], resume["pageId"]), (publication["publicationId"], "page-response"))
        # Then merely reaching the final page creates no answer or mastery evidence
        self.assertEqual(units.directory(chapter["chapterId"], learner_id="student-a")["lessons"][0]["answeredActivityCount"], 0)
        with self.app_store.engine.connect() as connection:
            self.assertEqual(connection.execute(select(func.count()).select_from(courseware_attempts)).scalar_one(), 0)
        # When an older device writes against version zero, Then the newer position remains intact
        with self.assertRaisesRegex(ValueError, "其他页面"):
            units.save_position(courseware_id, learner_id="student-a", request=CoursewarePositionSave(
                publicationId=publication["publicationId"], pageId="page-opening", expectedVersion=0,
                commandId="stale-device"), now=300)
        self.assertEqual(units.directory(chapter["chapterId"], learner_id="student-a")["resume"]["pageId"], "page-response")

    def test_user_reopens_old_version_then_attempts_are_not_applied_to_new_version_and_withdrawal_stops_progress(self) -> None:
        # Given a publication contains an activity with an incorrect response
        chapter, service, publication, units = self._published_unit()
        courseware_id = publication["coursewareId"]
        # Add a valid frozen activity through existing persistence to keep this scenario focused on progress semantics.
        first = copy.deepcopy(publication)
        first["snapshot"]["pages"][0]["blocks"].append({
            "kind": "activity", "blockId": "unit-choice", "activityId": "unit-choice", "activityKind": "choice",
            "instructions": "Which statement cites evidence?", "choices": ["a", "b"], "assessmentMode": "objective",
            "answerKey": ["a"], "selectionMode": "single", "sourceRefs": [], "authorship": "teacher_authored",
        })
        first.update(publicationId="unit-version-with-activity", version=2)
        service.store.save_publication(first)
        answer = CoursewareAttemptRequest(publicationId=first["publicationId"], activityId="unit-choice",
                                         commandId="wrong-answer", response={"selectedOptions": ["b"]})
        service.attempt(courseware_id, learner_id="student-a", request=answer)
        service.attempt(courseware_id, learner_id="student-a", request=answer)
        self.assertEqual(units.directory(chapter["chapterId"], learner_id="student-a")["lessons"][0]["needsCorrectionCount"], 1)
        units.save_position(courseware_id, learner_id="student-a", request=CoursewarePositionSave(
            publicationId=first["publicationId"], pageId="page-opening", expectedVersion=0, commandId="read-v2"), now=100)
        # When a new immutable version is published, Then the old resume remains explicit and new activity counts start at zero
        newer = copy.deepcopy(first)
        newer.update(publicationId="unit-version-newer", version=3)
        service.store.save_publication(newer)
        directory = units.directory(chapter["chapterId"], learner_id="student-a")
        self.assertEqual(directory["resume"]["publicationId"], first["publicationId"])
        self.assertEqual(directory["lessons"][0]["answeredActivityCount"], 0)
        # When the source chapter is withdrawn, Then old URLs cannot write new positions or leak unit content
        chapter["deletedAt"] = 1000
        self.app_store.save_chapter(chapter)
        self.assertEqual(units.list_units(learner_id="student-a"), {"items": []})
        with self.assertRaises(LookupError):
            units.save_position(courseware_id, learner_id="student-a", request=CoursewarePositionSave(
                publicationId=first["publicationId"], pageId="page-opening", expectedVersion=1, commandId="withdrawn"))

    def test_user_protected_student_resumes_own_unit_without_access_to_teacher_drafts(self) -> None:
        # Given two students with separate authenticated sessions and a published unit
        chapter, _service, publication, units = self._published_unit()
        with environment({"AUTH_MODE": "protected", "AUTH_COOKIE_SECURE": "1", "CORS_ORIGINS": "https://testserver",
                          "TEACHER_BOOTSTRAP_SECRET": "unit-acceptance-secret-" * 2}):
            app = create_app()
            auth = AuthSessionStore(engine=self.app_store.engine)
            app.state.auth_session_store = auth
            app.include_router(build_courseware_unit_router(units))
            with TestClient(app, base_url="https://testserver") as client:
                token, _ = auth.create_session(role="student", learner_id="student-a")
                client.cookies.set(COOKIE_NAME, token)
                path = f"/api/courseware/{publication['coursewareId']}"
                payload = {"publicationId": publication["publicationId"], "pageId": "page-response",
                           "expectedVersion": 0, "commandId": "protected-position"}
                # When the student saves or reads unit progress, Then own records are accessible
                self.assertEqual(client.get("/api/courseware-units").status_code, 200)
                self.assertEqual(client.get(f"{path}/unit").status_code, 200)
                self.assertEqual(client.put(f"{path}/progress", json=payload).status_code, 200)
                self.assertEqual(client.get(f"{path}/unit?learnerId=student-b").status_code, 403)
                self.assertEqual(client.put(f"{path}/progress?learnerId=student-b", json=payload).status_code, 403)
                # Then authoring directories and unsupported write methods remain teacher-only
                self.assertEqual(client.get(f"{path}/teaching-unit").status_code, 403)
                self.assertEqual(client.get(f"/api/chapters/{chapter['chapterId']}/courseware-unit").status_code, 403)
                self.assertEqual(client.post(f"{path}/unit", json={}).status_code, 403)
                token, _ = auth.create_session(role="student", learner_id="student-b")
                client.cookies.set(COOKIE_NAME, token)
                self.assertIsNone(client.get(f"{path}/unit").json()["resume"])
                client.cookies.clear()
                self.assertEqual(client.get("/api/courseware-units").status_code, 401)
