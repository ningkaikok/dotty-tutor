"""User scenarios for unattended courseware production through the real Worker."""

from typing import Any

from application.courseware_jobs import build_courseware_registry
from application.job_worker import JobWorker
from application.services.courseware_ai_review import CoursewareAiReviewService
from application.services.courseware_automation import CoursewareAutomationService
from domain.contracts.courseware import CoursewarePageContent, CoursewarePlanCreate
from persistence.ai_review_store import AiReviewStore
from tests import test_courseware_postgres_acceptance as fixtures
from tests.postgres_test_support import PostgresTestCase
from tests.test_courseware_ai_review_postgres import ScriptedReviewer


class CoursewareAutomationAcceptanceTests(PostgresTestCase):
    _source_course = fixtures.CoursewarePostgresAcceptanceTests._source_course

    def setUp(self) -> None:
        super().setUp()
        from application.services.chapter_courses import ChapterCourseService
        from persistence.app_store import AppStore
        from persistence.courseware_store import CoursewareStore
        from persistence.job_store import JobStore

        self.app_store = AppStore(database_url=self.database_url, data_root=self.data_root)
        self.jobs = JobStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.app_store.close)
        self.addCleanup(self.jobs.close)
        self.courseware_store = CoursewareStore(engine=self.app_store.engine)
        self.chapters = ChapterCourseService(self.app_store, self.jobs)

    def _start(self, reviewer: Any = None, *, automatic: bool = True):
        chapter, source, refs, service = self._source_course(distribution_scope="private")
        class RepairingRuntime(fixtures.DeterministicCoursewareRuntime):
            def generate_json(inner, prompt: str, schema: dict[str, Any], *, task: str, **kwargs: Any):
                output, run = super().generate_json(prompt, schema, task=task, **kwargs)
                if "Repair instructions from the previous check" in prompt:
                    output["blocks"][0]["text"] += " Repaired explanation."
                return output, run
        service.runtime = RepairingRuntime(refs, source["sourceRevisionId"])
        service.runtime.fail_second_page_once = False
        review = CoursewareAiReviewService(courseware=service, jobs=self.jobs,
            store=AiReviewStore(engine=self.app_store.engine), runtime=reviewer or ScriptedReviewer({}))
        service.automation = CoursewareAutomationService(courseware=service, reviewer=review)
        service.create_plan(chapter["chapterId"], CoursewarePlanCreate(
            subject="english", audience="Readers", languagePolicy="English", durationMinutes=10,
            objectives=["Read cited evidence"], expectedRecordVersion=1, pageStart=23, pageEnd=24,
            autoGeneratePages=True, autoPublish=automatic))
        worker = JobWorker(self.jobs, build_courseware_registry(service), worker_id="automatic-production")
        for _ in range(2):
            result = worker.run_once()
            self.assertEqual(result["status"], "succeeded", result.get("lastError"))
        document_id = service.list_chapter_documents(chapter["chapterId"])["items"][0]["coursewareId"]
        return service, worker, document_id

    def test_user_starts_automatic_production_then_receives_a_playable_private_lesson_without_approvals(self) -> None:
        # Given an automatically generated lesson, When its background checks complete
        service, worker, document_id = self._start()
        result = worker.run_once()
        self.assertEqual(result["status"], "succeeded", result.get("lastError"))
        # Then a private playable snapshot exists, with automation clearly attributed
        document = service.get_document(document_id)
        self.assertEqual(document["status"], "published")
        self.assertEqual(document["automation"]["status"], "ready")
        publication = service.get_presentation(document_id)
        self.assertEqual(publication["distributionScope"], "private")
        self.assertEqual(document["reviewRecords"][0]["decision"], "automated_approve")
        self.assertEqual(service.automation.reviewer.status(document_id)["metrics"]["compared"], 0)
        self.assertIsNone(worker.run_once())

    def test_user_gets_a_flagged_page_then_only_that_page_is_repaired_and_checked_again(self) -> None:
        # Given a reviewer finds a problem only in the first version of one page
        class RepairReviewer(ScriptedReviewer):
            def generate_json_as(inner, *args: Any, **kwargs: Any):
                if not inner.calls:
                    inner.calls.append({})
                    return {"verdict": "concern", "summary": "Clarify the explanation", "issues": []}, {}
                return super().generate_json_as(*args, **kwargs)

        service, worker, document_id = self._start(RepairReviewer({}))
        before = service.get_document(document_id)
        # When the checker, repair generator and follow-up checker run
        for _ in range(3):
            result = worker.run_once()
            self.assertEqual(result["status"], "succeeded", result.get("lastError"))
        after = service.get_document(document_id)
        # Then the unchanged page is retained and the lesson becomes playable automatically
        self.assertEqual(after["status"], "published")
        self.assertEqual(after["pages"][1]["version"], before["pages"][1]["version"])
        self.assertGreater(after["pages"][0]["version"], before["pages"][0]["version"])

    def test_user_gets_persistent_problems_then_repairs_stop_with_one_actionable_exception(self) -> None:
        # Given a reviewer keeps finding an unsupported explanation
        reviewer = ScriptedReviewer({"Notice the claim": {"verdict": "concern", "summary": "Check the claim", "issues": []}})
        service, worker, document_id = self._start(reviewer)
        # When bounded automatic repairs are exhausted
        for _ in range(5):
            result = worker.run_once()
            self.assertEqual(result["status"], "succeeded", result.get("lastError"))
        document = service.get_document(document_id)
        # Then no publication is created and only the unresolved problem asks for intervention
        self.assertIsNone(document["publicationId"])
        self.assertEqual(document["automation"]["status"], "needs_attention")
        self.assertIn("Check the claim", document["automation"]["message"])
        self.assertIsNone(worker.run_once())

    def test_user_edits_while_checks_are_queued_then_automation_preserves_the_edit(self) -> None:
        # Given background checking is queued, When the user edits the lesson
        service, worker, document_id = self._start()
        document = service.get_document(document_id)
        page = document["pages"][0]
        editable = {key: page[key] for key in CoursewarePageContent.model_fields}
        editable["revealSteps"] = []
        content = CoursewarePageContent.model_validate(editable)
        content.teacherNotes = "Keep this manual correction."
        service.edit_page(document_id, page["pageId"], content=content, expected_record_version=document["recordVersion"])
        worker.run_once()
        # Then a queued automatic decision cannot overwrite or publish the user's revision
        saved = service.get_document(document_id)
        self.assertEqual(saved["pages"][0]["teacherNotes"], "Keep this manual correction.")
        self.assertEqual(saved["automation"]["status"], "paused")
        self.assertIsNone(saved["publicationId"])

    def test_user_chooses_manual_production_then_no_background_approval_is_added(self) -> None:
        # Given the existing manual workflow, When pages complete
        service, worker, document_id = self._start(automatic=False)
        # Then the lesson remains pending and the automatic service queues nothing
        document = service.get_document(document_id)
        self.assertIsNone(document.get("automation"))
        self.assertEqual(document["reviewRecords"], [])
        self.assertIsNone(worker.run_once())

    def test_user_cancels_queued_checking_then_the_lesson_is_not_published_or_left_spinning(self) -> None:
        # Given checking is queued, When the user cancels that background task
        service, worker, document_id = self._start()
        document = service.get_document(document_id)
        self.jobs.request_cancel(document["automation"]["jobId"])
        # Then the read view shows the exception without manufacturing a publication
        saved = service.get_document(document_id)
        self.assertEqual(saved["automation"]["status"], "needs_attention")
        self.assertIsNone(saved["publicationId"])
        self.assertIsNone(worker.run_once())

    def test_user_loses_a_checking_worker_then_retry_reuses_its_input_and_can_complete(self) -> None:
        import time

        # Given a checker loses its lease until its bounded attempt budget expires
        service, worker, document_id = self._start()
        job_id = service.get_document(document_id)["automation"]["jobId"]
        clock = time.time()
        self.jobs.claim_next("lost-worker", lease_seconds=1, now=clock)
        self.jobs.recover_expired_leases(now=clock + 2)
        self.jobs.claim_next("lost-again", lease_seconds=1, now=clock + 3)
        self.jobs.recover_expired_leases(now=clock + 5)
        # Then it is surfaced as one retryable exception, including in the library
        document = service.get_document(document_id)
        self.assertEqual(document["automation"]["status"], "needs_attention")
        self.assertTrue(document["automation"]["retryable"])
        # When explicitly retried, Then unchanged frozen inputs remain valid and complete
        self.jobs.retry_job(job_id)
        result = worker.run_once()
        self.assertEqual(result["status"], "succeeded", result.get("lastError"))
        self.assertEqual(service.get_document(document_id)["status"], "published")
