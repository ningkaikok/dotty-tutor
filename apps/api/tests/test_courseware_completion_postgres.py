"""Given/When/Then persistence and access boundaries for completion preparation."""
from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from application import create_app
from application.services.chapter_courses import ChapterCourseService
from application.services.courseware import CoursewareService
from domain.chapters.source import pages_from_ocr
from domain.contracts.courseware import (
    CoursewarePageContent,
    CoursewarePlanCreate,
    CoursewarePlanEdit,
)
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
                     "planVersion": 1, "generationStatus": "completed", "pages": [
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
        # And the finished approved plan is offered a supplement rather than a replacement
        self.assertTrue(first["canCreatePlan"])
        self.assertTrue(first["supplementsExistingPlan"])
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

    # --- Incremental unit completion (supplement) -------------------------------------------

    def _request(self, start: int, end: int, *, supplement: bool) -> CoursewarePlanCreate:
        chapter = self.chapters.get(self.chapter["chapterId"])
        return CoursewarePlanCreate(
            subject="science", audience="Teachers", languagePolicy="Chinese", durationMinutes=10,
            objectives=["Observe evidence"], pageStart=start, pageEnd=end,
            expectedRecordVersion=int(chapter["recordVersion"]), supplement=supplement,
        )

    def _approve(self, start: int, end: int, objective_id: str, *, supplement: bool) -> dict[str, Any]:
        """Teacher creates a plan for a page range, fills the outline, and confirms it."""
        chapter_id = self.chapter["chapterId"]
        created = self.service.create_plan(chapter_id, self._request(start, end, supplement=supplement))["plan"]
        numbers = list(range(start, end + 1))
        minutes = 10 // len(numbers)
        edited = self.service.edit_plan(chapter_id, CoursewarePlanEdit.model_validate({
            "audience": "Teachers", "languagePolicy": "Chinese", "expectedRecordVersion": created["recordVersion"],
            "pageStart": start, "pageEnd": end,
            "objectives": [{"objectiveId": objective_id, "description": "Observe evidence",
                            "sourceRefs": [self._ref(numbers[0])]}],
            "sessions": [{"sessionId": f"session-{start}", "title": f"Pages {start}-{end}",
                       "durationMinutes": minutes * len(numbers),
                       "sections": [{"sectionId": f"section-{start}", "title": "Observe",
                                     "estimatedMinutes": minutes * len(numbers),
                                     "pages": [{"pageId": f"page-{number}", "title": f"Page {number}",
                                                "pageKind": "explanation", "objectiveIds": [objective_id],
                                                "sourceRefs": [self._ref(number)], "estimatedMinutes": minutes}
                                               for number in numbers]}]}],
        }))
        return self.service.approve_plan(
            chapter_id, plan_version=edited["planVersion"], expected_record_version=edited["recordVersion"],
            command_id=f"approve-{start}-{end}",
        )

    def _mark_generated(self, courseware_id: str) -> dict[str, Any]:
        """Stand-in for the model worker: the first batch has finished generating."""
        document = self.store.load_document(courseware_id)
        assert document is not None
        for page in document["pages"]:
            page["generationStatus"] = "completed"
        document["generationStatus"] = "completed"
        return self.store.save_record(
            record_id=f"document:{courseware_id}", record_type="document", chapter_id=self.chapter["chapterId"],
            courseware_id=courseware_id, source_revision_id=self.source["sourceRevisionId"], payload=document,
            status="draft", expected_version=int(document["recordVersion"]),
        )

    def test_user_supplements_remaining_pages_then_confirmed_lessons_stay_editable_and_listed(self) -> None:
        # Given a confirmed and generated lesson for pages 1–2
        first = self._approve(1, 2, "objective-first", supplement=False)
        first_id = first["documents"][0]["coursewareId"]
        generated = self._mark_generated(first_id)
        # When the teacher supplements the remaining ready pages 3–4
        second = self._approve(3, 4, "objective-second", supplement=True)
        second_id = second["documents"][0]["coursewareId"]
        # Then both lessons are listed and the first is still readable with its own objectives
        listed = {item["coursewareId"] for item in self.service.list_chapter_documents(self.chapter["chapterId"])["items"]}
        self.assertEqual(listed, {first_id, second_id})
        self.assertEqual(self.service.get_document(first_id)["coursewareId"], first_id)
        plan = self.service.get_plan(self.chapter["chapterId"])
        assert plan is not None
        self.assertEqual([batch["planVersion"] for batch in plan["retainedBatches"]], [first["plan"]["planVersion"]])
        self.assertEqual(plan["retainedBatches"][0]["objectives"][0]["objectiveId"], "objective-first")
        # And editing the first lesson accepts its frozen objective but not the new batch's objective
        page = generated["pages"][0]
        content = {key: page[key] for key in ("title", "pageKind", "layout", "sourceRefs", "estimatedMinutes")}
        edited = self.service.edit_page(first_id, page["pageId"], expected_record_version=generated["recordVersion"],
                                        content=CoursewarePageContent(**content, objectiveIds=["objective-first"]))
        self.assertEqual(edited["coursewareId"], first_id)
        with self.assertRaisesRegex(ValueError, "已声明的教学目标"):
            self.service.edit_page(first_id, page["pageId"], expected_record_version=edited["recordVersion"],
                                   content=CoursewarePageContent(**content, objectiveIds=["objective-second"]))
        # And completion now treats pages 1–4 as planned, leaving only 6–7 to suggest
        report = self.service.get_completion(self.chapter["chapterId"])
        self.assertEqual(report["alreadyPlannedPages"], [1, 2, 3, 4])

    def test_user_supplements_pages_that_already_have_lessons_then_the_plan_is_unchanged(self) -> None:
        # Given a confirmed lesson for pages 1–2
        self._approve(1, 2, "objective-first", supplement=False)
        before = self.store.load_plan(self.chapter["chapterId"])
        jobs_before = self.jobs.list_jobs()
        # When a supplement range overlaps page 2, Then it is rejected before any plan or model job is written
        with self.assertRaisesRegex(ValueError, "补齐范围包含"):
            self.service.create_plan(self.chapter["chapterId"], self._request(2, 3, supplement=True))
        self.assertEqual(self.store.load_plan(self.chapter["chapterId"]), before)
        self.assertEqual(self.jobs.list_jobs(), jobs_before)

    def test_user_supplements_while_the_current_plan_is_unconfirmed_then_that_work_is_protected(self) -> None:
        # Given an outline for pages 1–2 still awaiting the teacher's confirmation
        self.service.create_plan(self.chapter["chapterId"], self._request(1, 2, supplement=False))
        before = self.store.load_plan(self.chapter["chapterId"])
        # When the teacher tries to supplement pages 3–4, Then the open plan is not discarded
        with self.assertRaisesRegex(ValueError, "当前规划尚未确认"):
            self.service.create_plan(self.chapter["chapterId"], self._request(3, 4, supplement=True))
        self.assertEqual(self.store.load_plan(self.chapter["chapterId"]), before)

    def test_user_edits_a_supplement_plan_into_retained_pages_then_the_edit_is_rejected(self) -> None:
        # Given lessons for pages 1–2 and a supplement plan for pages 3–4
        self._approve(1, 2, "objective-first", supplement=False)
        draft = self.service.create_plan(self.chapter["chapterId"], self._request(3, 4, supplement=True))["plan"]
        # When the teacher widens the supplement to page 2, Then the retained lesson's page cannot be reclaimed
        with self.assertRaisesRegex(ValueError, "已保留课时"):
            self.service.edit_plan(self.chapter["chapterId"], CoursewarePlanEdit.model_validate({
                "audience": "Teachers", "languagePolicy": "Chinese", "expectedRecordVersion": draft["recordVersion"],
                "pageStart": 2, "pageEnd": 4,
                "objectives": [{"objectiveId": "o", "description": "Observe", "sourceRefs": [self._ref(3)]}],
                "sessions": [{"sessionId": "s", "title": "S", "durationMinutes": 10, "sections": [
                    {"sectionId": "x", "title": "X", "estimatedMinutes": 10, "pages": [
                        {"pageId": "p", "title": "P", "pageKind": "explanation", "objectiveIds": ["o"],
                         "sourceRefs": [self._ref(3)], "estimatedMinutes": 10}]}]}],
            }))

    def test_user_replaces_the_whole_plan_then_previous_drafts_are_superseded_as_before(self) -> None:
        # Given a confirmed lesson for pages 1–2
        first = self._approve(1, 2, "objective-first", supplement=False)
        first_id = first["documents"][0]["coursewareId"]
        # When the teacher creates an ordinary (non-supplement) plan
        self.service.create_plan(self.chapter["chapterId"], self._request(1, 4, supplement=False))
        # Then the old draft is superseded and its pages become gaps again
        with self.assertRaisesRegex(LookupError, "已被新版本取代"):
            self.service.get_document(first_id)
        self.assertEqual(self.service.list_chapter_documents(self.chapter["chapterId"])["items"], [])
        self.assertEqual(self.service.get_coverage(self.chapter["chapterId"])["generatedPages"], [])
