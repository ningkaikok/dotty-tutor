"""Given/When/Then persistence and access boundaries for completion preparation."""
from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from application import create_app
from application.services.chapter_courses import ChapterCourseService
from application.services.courseware import CoursewareService
from domain.chapters.source import pages_from_ocr
from domain.contracts.courseware import CoursewarePlanCreate
from persistence.app_store import AppStore
from persistence.auth_store import AuthSessionStore
from persistence.courseware_store import CoursewareStore
from persistence.job_store import JobStore
from routers.auth_routes import COOKIE_NAME
from routers.courseware_routes import build_courseware_router
from tests.auth_test_support import environment
from tests.postgres_test_support import PostgresTestCase


class CoursewareCompletionPostgresTests(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.app_store = AppStore(database_url=self.database_url, data_root=self.data_root)
        self.jobs = JobStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.app_store.close)
        self.addCleanup(self.jobs.close)
        self.store = CoursewareStore(engine=self.app_store.engine)
        self.chapters = ChapterCourseService(self.app_store, self.jobs)
        self.service = CoursewareService(store=self.store, chapters=self.chapters, jobs=self.jobs)
        directory = self.app_store.upload_root / "completion-fixture"
        directory.mkdir(parents=True)
        self.original = directory / "source.pdf"
        writer = PdfWriter()
        for _ in range(8):
            writer.add_blank_page(width=100, height=100)
        writer.write(self.original)
        text = "\n".join(f"<!-- page {number} -->\nObserve evidence on page {number}." for number in (1, 2, 3, 4, 6, 7, 8))
        self.app_store.save_job({
            "uploadId": "completion-fixture", "filename": "Engineering source.pdf", "contentType": "application/pdf",
            "size": self.original.stat().st_size, "chunkSize": 1024, "totalChunks": 1,
            "directory": directory, "status": "complete", "sourceText": text,
        })
        pages = pages_from_ocr(text)
        next(page for page in pages if page["page"] == 8)["flags"] = ["unreadable"]
        self.chapter = self.chapters.create({
            "title": "Engineering unit", "subject": "science", "teachingMode": "tutorial",
            "source": {"uploadId": "completion-fixture", "license": "CC BY 4.0", "attributionText": "Engineering fixture",
                       "distributionScope": "private", "pageStart": 1, "pageEnd": 8, "pages": pages},
        })
        self.source = self.chapter["sourceRevisions"][-1]

    def _ref(self, number: int) -> dict[str, Any]:
        page = next(item for item in self.source["pages"] if item["page"] == number)
        return {"sourceRevisionId": self.source["sourceRevisionId"], "page": number,
                "sentenceIds": [page["sentences"][0]["sentenceId"]]}

    def _plan(self, *, active: bool = False) -> dict[str, Any]:
        payload = {
            "sourceRevisionId": self.source["sourceRevisionId"], "pageStart": 2, "pageEnd": 3,
            "planVersion": 1, "status": "draft" if active else "approved",
            "generationStatus": "queued" if active else "completed",
            "generationJobId": "completion-plan-job" if active else None,
            "sessions": [{"sections": [{"pages": [{"sourceRefs": [self._ref(n)]} for n in (2, 3)]}]}],
        }
        return self.store.save_record(
            record_id=f"plan:{self.chapter['chapterId']}", record_type="plan", chapter_id=self.chapter["chapterId"],
            courseware_id=None, source_revision_id=self.source["sourceRevisionId"], payload=payload,
            status=payload["status"], expected_version=0,
        )

    def test_user_reads_completion_then_existing_approved_plan_and_generated_content_are_unchanged(self) -> None:
        # Given approved planning for pages 2/3 and generated pages 1/4, with source gaps 5/8
        plan = self._plan()
        self.store.save_record(
            record_id="document:completion-document", record_type="document", chapter_id=self.chapter["chapterId"],
            courseware_id="completion-document", source_revision_id=self.source["sourceRevisionId"],
            payload={"coursewareId": "completion-document", "sourceRevisionId": self.source["sourceRevisionId"],
                     "generationStatus": "completed", "pages": [
                         {"sourceRefs": [self._ref(1)], "generationStatus": "completed", "reviewStatus": "pending"},
                         {"sourceRefs": [self._ref(4)], "generationStatus": "completed", "reviewStatus": "approved"}]},
            status="in_review", expected_version=0,
        )
        before = self.store.list_documents(self.chapter["chapterId"])
        before_jobs = self.jobs.list_jobs()
        # When a teacher reads completion preparation repeatedly
        first = self.service.get_completion(self.chapter["chapterId"])
        second = self.service.get_completion(self.chapter["chapterId"])
        # Then only 6/7 are suggestions and preparation has no persistence side effects
        self.assertEqual(first, second)
        self.assertEqual(first["batches"], [{"pageStart": 6, "pageEnd": 7, "sourcePages": [6, 7]}])
        self.assertEqual(first["blockedPages"], [5, 8])
        self.assertEqual(first["pendingReviewPages"], [1])
        self.assertEqual(first["pendingGenerationPages"], [2, 3])
        self.assertFalse(first["canCreatePlan"])
        self.assertEqual(self.store.load_plan(self.chapter["chapterId"]), plan)
        self.assertEqual(self.store.list_documents(self.chapter["chapterId"]), before)
        self.assertEqual(self.jobs.list_jobs(), before_jobs)

    def test_user_reads_suggestions_during_an_active_job_then_the_original_job_is_not_cancelled(self) -> None:
        # Given the current-source plan has a queued model job
        plan = self._plan(active=True)
        job = self.jobs.create_job("courseware.plan.generate", {"chapterId": self.chapter["chapterId"]}, job_id="completion-plan-job")
        # When a teacher reads new page ranges, Then the original generation remains authoritative
        report = self.service.get_completion(self.chapter["chapterId"])
        self.assertEqual(report["action"], "wait_generation")
        self.assertFalse(report["canCreatePlan"])
        self.assertEqual(self.jobs.get_job(job["jobId"]), job)
        self.assertEqual(self.store.load_plan(self.chapter["chapterId"]), plan)

    def test_user_replaces_the_source_file_or_deletes_the_chapter_then_no_stale_suggestion_is_returned(self) -> None:
        # Given a source pinned to the original PDF hash
        # When that physical file changes, Then preparation rejects it rather than planning against stale text
        original_bytes = self.original.read_bytes()
        self.original.write_bytes(original_bytes + b"\nchanged")
        with self.assertRaisesRegex(ValueError, "原文件已变化"):
            self.service.get_completion(self.chapter["chapterId"])
        self.original.write_bytes(original_bytes)
        # When the teacher withdraws the chapter, Then the same endpoint rejects the source
        self.chapters.delete(self.chapter["chapterId"])
        with self.assertRaises(LookupError):
            self.service.get_completion(self.chapter["chapterId"])

    def test_user_reads_restored_planning_without_a_job_service_then_saved_generation_state_is_visible(self) -> None:
        # Given a restored plan retains its job ID but no worker service is attached
        self._plan(active=True)
        offline = CoursewareService(store=self.store, chapters=self.chapters, jobs=None)
        # When preparing the missing ranges, Then stored in-flight state stays protected without a job lookup
        report = offline.get_completion(self.chapter["chapterId"])
        self.assertEqual(report["action"], "wait_generation")
        self.assertFalse(report["canCreatePlan"])
        self.assertEqual(report["pendingGenerationPages"], [2, 3])

    def test_user_updates_the_source_after_reading_suggestions_then_old_preparation_cannot_create_a_plan(self) -> None:
        # Given an approved plan and preparation pinned to the original chapter record version
        plan = self._plan()
        before = self.service.get_completion(self.chapter["chapterId"])
        # When the source is revised before the teacher submits a selected range
        revised = self.chapters.revise(self.chapter["chapterId"], {
            "expectedRecordVersion": before["expectedRecordVersion"],
            "source": {"uploadId": "completion-fixture", "sourceVersion": "2", "pageStart": 1, "pageEnd": 8,
                       "license": "CC BY 4.0", "attributionText": "Engineering fixture", "pages": self.source["pages"]},
        })
        # Then stale planning is displayed explicitly and the ordinary version guard rejects the old selection
        after = self.service.get_completion(self.chapter["chapterId"])
        self.assertTrue(after["existingPlan"]["stale"])
        self.assertEqual(after["sourceRevisionId"], revised["sourceRevisions"][-1]["sourceRevisionId"])
        self.assertEqual(after["alreadyPlannedPages"], [])
        with self.assertRaisesRegex(ValueError, "教材章节已更新"):
            self.service.create_plan(self.chapter["chapterId"], CoursewarePlanCreate(
                subject="science", audience="Teachers", languagePolicy="Chinese",
                durationMinutes=10, objectives=["Observe evidence"], pageStart=6, pageEnd=7,
                expectedRecordVersion=before["expectedRecordVersion"],
            ))
        self.assertEqual(self.store.load_plan(self.chapter["chapterId"]), plan)
        self.assertEqual(self.jobs.list_jobs(), [])

    def test_user_deletes_a_draft_then_its_generated_pages_and_old_job_do_not_hide_completion_gaps(self) -> None:
        # Given a legacy deleted draft whose row lacks a deletion timestamp
        self.store.save_record(
            record_id="document:deleted-draft", record_type="document", chapter_id=self.chapter["chapterId"],
            courseware_id="deleted-draft", source_revision_id=self.source["sourceRevisionId"], status="deleted",
            payload={"coursewareId": "deleted-draft", "status": "deleted", "sourceRevisionId": self.source["sourceRevisionId"],
                     "generationStatus": "running", "generationJobId": "deleted-job",
                     "pages": [{"sourceRefs": [self._ref(1)], "generationStatus": "completed", "reviewStatus": "approved"}]},
            expected_version=0,
        )
        # When reading coverage and preparation, Then the withdrawn draft cannot suppress or block new page preparation
        coverage = self.service.get_coverage(self.chapter["chapterId"])
        report = self.service.get_completion(self.chapter["chapterId"])
        self.assertEqual(coverage["generatedPages"], [])
        self.assertEqual(coverage["reviewedPages"], [])
        self.assertEqual(report["alreadyGeneratedPages"], [])
        self.assertEqual(report["action"], "create_plan")
        self.assertTrue(report["canCreatePlan"])
        self.assertIn(1, report["batches"][0]["sourcePages"])

    def test_user_requests_completion_in_protected_mode_then_only_teachers_can_read_bounded_suggestions(self) -> None:
        # Given durable protected-mode sessions for a teacher and a student
        self.enterContext(environment({"AUTH_MODE": "protected", "AUTH_COOKIE_SECURE": "1",
            "TEACHER_BOOTSTRAP_SECRET": "completion-engineering-test-secret-" * 2,
            "CORS_ORIGINS": "https://testserver"}))
        auth = AuthSessionStore(engine=self.app_store.engine)
        app = create_app()
        app.state.auth_session_store = auth
        app.include_router(build_courseware_router(self.service))
        client = self.enterContext(TestClient(app, base_url="https://testserver"))
        path = f"/api/chapters/{self.chapter['chapterId']}/courseware-completion"
        teacher_token, _ = auth.create_session(role="teacher", learner_id=None)
        student_token, _ = auth.create_session(role="student", learner_id="completion-learner")
        # When the student requests this preparation data, Then the source remains teacher-only
        client.cookies.set(COOKIE_NAME, student_token)
        self.assertEqual(client.get(path).status_code, 403)
        # When a teacher specifies two-page batches, Then the typed response respects the requested limit
        client.cookies.set(COOKIE_NAME, teacher_token)
        result = client.get(path, params={"maxPagesPerBatch": 2})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["maxPagesPerBatch"], 2)
        self.assertTrue(all(len(batch["sourcePages"]) <= 2 for batch in result.json()["batches"]))
        self.assertEqual(client.get(path, params={"maxPagesPerBatch": 21}).status_code, 422)
