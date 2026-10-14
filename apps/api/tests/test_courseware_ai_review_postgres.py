"""Given/When/Then acceptance for advisory AI pre-review of courseware and source cases."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from application import create_app
from application.ai_review_jobs import build_ai_review_registry
from application.job_worker import JobExecutionContext, RetryableJobError
from application.services.chapter_courses import ChapterCourseService
from application.services.chapter_source_preview import file_hash
from application.services.courseware import CoursewareService
from application.services.courseware_ai_review import CoursewareAiReviewService
from application.services.quality_review import QualityReviewService
from domain.chapters.source import pages_from_ocr
from domain.contracts.courseware import (
    CoursewarePageContent,
    CoursewarePlanCreate,
    CoursewarePlanEdit,
)
from persistence.ai_review_store import AiReviewStore
from persistence.app_store import AppStore
from persistence.auth_store import AuthSessionStore
from persistence.courseware_store import CoursewareStore
from persistence.job_store import JobStore
from persistence.quality_review_store import QualityReviewStore
from routers.auth_routes import COOKIE_NAME
from routers.courseware_routes import build_courseware_router
from tests.auth_test_support import environment
from tests.postgres_test_support import PostgresTestCase
from tests.test_quality_review_postgres import _pdf_with_text

BLOCKING = {"code": "formula_error", "severity": "blocking", "message": "公式与原页不一致", "sourcePage": 2}


class ScriptedReviewer:
    """External-boundary stand-in: returns a scripted verdict per page title and records calls."""

    def __init__(self, verdicts: dict[str, dict[str, Any]]) -> None:
        self.verdicts = verdicts
        self.calls: list[dict[str, Any]] = []

    def generate_json_as(self, provider: str, model: str, prompt: str, schema: dict[str, Any],
                         max_tokens: int = 1200, image_paths: list[Path] | None = None, **_: Any):
        self.calls.append({"provider": provider, "model": model, "prompt": prompt, "images": list(image_paths or [])})
        output = next((value for title, value in self.verdicts.items() if title in prompt),
                      {"verdict": "pass", "summary": "未发现问题", "issues": []})
        return output, {"actualProvider": provider, "actualModel": model, "durationMs": 5}


class CoursewareAiReviewAcceptanceTests(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.app_store = AppStore(database_url=self.database_url, data_root=self.data_root)
        self.jobs = JobStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.app_store.close)
        self.addCleanup(self.jobs.close)
        self.chapters = ChapterCourseService(self.app_store, self.jobs)
        self.courseware = CoursewareService(store=CoursewareStore(engine=self.app_store.engine),
                                            chapters=self.chapters, jobs=self.jobs)
        directory = self.app_store.upload_root / "ai-review-fixture"
        directory.mkdir(parents=True)
        writer = PdfWriter()
        for _ in range(3):
            writer.add_blank_page(width=100, height=100)
        writer.write(directory / "source.pdf")
        text = "\n".join(f"<!-- page {n} -->\nThe area of a square with side {n} is {n * n}." for n in (1, 2, 3))
        self.app_store.save_job({
            "uploadId": "ai-review-fixture", "filename": "Geometry.pdf", "contentType": "application/pdf",
            "size": (directory / "source.pdf").stat().st_size, "chunkSize": 1024, "totalChunks": 1,
            "directory": directory, "status": "complete", "sourceText": text,
        })
        self.chapter = self.chapters.create({
            "title": "Squares", "subject": "math", "teachingMode": "tutorial",
            "source": {"uploadId": "ai-review-fixture", "license": "CC BY 4.0", "attributionText": "Fixture",
                       "distributionScope": "private", "pageStart": 1, "pageEnd": 3, "pages": pages_from_ocr(text)},
        })
        self.source = self.chapter["sourceRevisions"][-1]
        self.courseware_id = self._generated_lesson()

    def _ref(self, number: int) -> dict[str, Any]:
        page = next(item for item in self.source["pages"] if item["page"] == number)
        return {"sourceRevisionId": self.source["sourceRevisionId"], "page": number,
                "sentenceIds": [page["sentences"][0]["sentenceId"]]}

    def _generated_lesson(self) -> str:
        """A confirmed three-page lesson whose pages finished generating (worker stand-in)."""
        chapter_id = self.chapter["chapterId"]
        created = self.courseware.create_plan(chapter_id, CoursewarePlanCreate(
            subject="math", audience="Grade 7", languagePolicy="Chinese", durationMinutes=12,
            objectives=["Compute square areas"], pageStart=1, pageEnd=3, expectedRecordVersion=1,
        ))["plan"]
        edited = self.courseware.edit_plan(chapter_id, CoursewarePlanEdit.model_validate({
            "audience": "Grade 7", "languagePolicy": "Chinese", "expectedRecordVersion": created["recordVersion"],
            "pageStart": 1, "pageEnd": 3,
            "objectives": [{"objectiveId": "area", "description": "Compute square areas", "sourceRefs": [self._ref(1)]}],
            "sessions": [{"sessionId": "s", "title": "Areas", "durationMinutes": 12, "sections": [
                {"sectionId": "x", "title": "Explain", "estimatedMinutes": 12, "pages": [
                    {"pageId": f"page-{n}", "title": title, "pageKind": "explanation", "objectiveIds": ["area"],
                     "sourceRefs": [self._ref(n)], "estimatedMinutes": 4}
                    for n, title in ((1, "Side one"), (2, "Side two"), (3, "Side three"))]}]}],
        }))
        approved = self.courseware.approve_plan(chapter_id, plan_version=edited["planVersion"],
                                                expected_record_version=edited["recordVersion"], command_id="approve")
        courseware_id = approved["documents"][0]["coursewareId"]
        document = self.courseware.store.load_document(courseware_id)
        assert document is not None
        for page in document["pages"]:
            page["generationStatus"] = "completed"
            page["blocks"] = [{"blockId": f"{page['pageId']}-text", "kind": "text",
                               "text": f"Explain {page['title']}.", "authorship": "ai_supplement"}]
        document["generationStatus"] = "completed"
        self.courseware.store.save_record(
            record_id=f"document:{courseware_id}", record_type="document", chapter_id=chapter_id,
            courseware_id=courseware_id, source_revision_id=self.source["sourceRevisionId"], payload=document,
            status="draft", expected_version=int(document["recordVersion"]),
        )
        return courseware_id

    def _service(self, reviewer: ScriptedReviewer, *, rate: float, quality_review: Any = None) -> CoursewareAiReviewService:
        return CoursewareAiReviewService(courseware=self.courseware, store=AiReviewStore(engine=self.app_store.engine),
                                         jobs=self.jobs, runtime=reviewer, quality_review=quality_review,
                                         sampling_rate=rate)

    def _run_job(self, service: CoursewareAiReviewService, job_id: str, handler: str = "courseware.ai_review") -> dict[str, Any]:
        job = self.jobs.get_job(job_id)
        assert job is not None
        context = JobExecutionContext(job_id=job_id, worker_id="test", attempt_count=1, lease_expires_at=time.time() + 60)
        return build_ai_review_registry(service).run(handler, job["payload"], lambda: False, context)

    def _document(self) -> dict[str, Any]:
        return self.courseware.get_document(self.courseware_id)

    def test_user_runs_ai_pre_review_then_only_flagged_pages_need_reading_and_nothing_is_approved(self) -> None:
        # Given a three-page lesson where the reviewer finds a formula error on "Side two"
        reviewer = ScriptedReviewer({"Side two": {"verdict": "fail", "summary": "面积算错", "issues": [BLOCKING]}})
        service = self._service(reviewer, rate=0.0)
        # When the teacher starts AI pre-review, Then the estimate is one model call per page
        start = service.request(self.courseware_id, expected_record_version=self._document()["recordVersion"])
        self.assertEqual((start["pagesToReview"], start["cachedPages"]), (3, 0))
        self._run_job(service, start["jobId"])
        # Then each page got exactly one reviewer call and the flagged page goes to the teacher's queue
        self.assertEqual(len(reviewer.calls), 3)
        routes = {page["pageId"]: page["route"] for page in service.status(self.courseware_id)["pages"]}
        self.assertEqual(routes, {"page-1": "bulk_confirmable", "page-2": "needs_review", "page-3": "bulk_confirmable"})
        flagged = next(page for page in service.status(self.courseware_id)["pages"] if page["pageId"] == "page-2")
        self.assertEqual(flagged["issues"][0]["code"], "formula_error")
        # And the AI approved nothing: every page still awaits the teacher and the lesson cannot be published
        self.assertTrue(all(page["reviewStatus"] == "pending" for page in self._document()["pages"]))
        with self.assertRaisesRegex(ValueError, "审核"):
            self.courseware.publish(self.courseware_id, expected_record_version=self._document()["recordVersion"])

    def test_user_requests_pre_review_again_then_unchanged_pages_are_not_sent_to_the_model(self) -> None:
        # Given a completed pre-review
        reviewer = ScriptedReviewer({})
        service = self._service(reviewer, rate=0.0)
        self._run_job(service, service.request(self.courseware_id, expected_record_version=self._document()["recordVersion"])["jobId"])
        # When the teacher asks again without changing anything, Then no job and no model call is made
        again = service.request(self.courseware_id, expected_record_version=self._document()["recordVersion"])
        self.assertEqual((again["pagesToReview"], again["cachedPages"], again["jobId"]), (0, 3, None))
        self.assertEqual(len(reviewer.calls), 3)

    def test_user_edits_a_page_after_pre_review_then_its_old_verdict_cannot_route_it(self) -> None:
        # Given a pre-reviewed lesson
        service = self._service(ScriptedReviewer({}), rate=0.0)
        self._run_job(service, service.request(self.courseware_id, expected_record_version=self._document()["recordVersion"])["jobId"])
        # When the teacher edits page 1
        document = self._document()
        page = next(item for item in document["pages"] if item["pageId"] == "page-1")
        content = {key: page[key] for key in ("title", "pageKind", "layout", "objectiveIds", "sourceRefs", "estimatedMinutes")}
        self.courseware.edit_page(self.courseware_id, "page-1", expected_record_version=document["recordVersion"],
                                  content=CoursewarePageContent.model_validate({**content, "blocks": [
                                      {"blockId": "edited", "kind": "text", "text": "Edited explanation."}]}))
        # Then the old verdict is stale and only that page would be sent again
        status = service.status(self.courseware_id)
        self.assertEqual(next(item for item in status["pages"] if item["pageId"] == "page-1")["route"], "stale")
        self.assertEqual(status["pagesToReview"], 1)

    def test_user_checks_sampled_pages_then_misses_and_false_alarms_are_measured(self) -> None:
        # Given every AI pass is drawn for a spot check, and "Side two" is (wrongly) failed by the AI
        reviewer = ScriptedReviewer({"Side two": {"verdict": "fail", "summary": "疑似错误", "issues": [BLOCKING]}})
        service = self._service(reviewer, rate=1.0)
        self._run_job(service, service.request(self.courseware_id, expected_record_version=self._document()["recordVersion"])["jobId"])
        self.assertTrue(all(page["route"] == "needs_review" for page in service.status(self.courseware_id)["pages"]))
        # When the teacher approves the AI-failed page and requests changes on an AI-passed page
        version = self._document()["recordVersion"]
        self.courseware.review_document(self.courseware_id, decision="approve", page_ids=["page-2"], note="",
                                        expected_record_version=version, actor_id="teacher")
        self.courseware.review_document(self.courseware_id, decision="request_changes", page_ids=["page-1"],
                                        note="例题条件缺失", expected_record_version=version + 1, actor_id="teacher")
        # Then one miss and one false alarm are recorded, and decided pages leave the AI queue
        status = service.status(self.courseware_id)
        self.assertEqual(status["metrics"], {"compared": 2, "agreements": 0, "missed": 1, "falseAlarms": 1})
        self.assertEqual({page["pageId"]: page["route"] for page in status["pages"]}["page-2"], "decided")

    def test_user_gets_a_contradictory_model_answer_then_the_job_retries_instead_of_routing_it(self) -> None:
        # Given a reviewer that "passes" a page while reporting a blocking problem
        reviewer = ScriptedReviewer({"Side one": {"verdict": "pass", "summary": "ok", "issues": [BLOCKING]}})
        service = self._service(reviewer, rate=0.0)
        job_id = service.request(self.courseware_id, expected_record_version=self._document()["recordVersion"])["jobId"]
        # When the job runs, Then the malformed verdict is retried and never stored as a pass
        with self.assertRaises(RetryableJobError):
            self._run_job(service, job_id)
        self.assertEqual(next(page for page in service.status(self.courseware_id)["pages"]
                              if page["pageId"] == "page-1")["route"], "not_reviewed")

    def test_user_asks_for_a_source_case_opinion_then_it_stays_advisory(self) -> None:
        # Given an imported source case backed by a managed PDF
        store = QualityReviewStore(database_url=self.database_url, data_root=str(self.data_root))
        self.addCleanup(store.close)
        source_root = self.data_root / "sources"
        source_root.mkdir()
        pdf = source_root / "ElementaryAlgebra2e-WEB.pdf"
        pdf.write_bytes(_pdf_with_text("Verified source quote for the test."))
        quality_review = QualityReviewService(store, source_root=source_root)
        package = quality_review.import_package({"title": "Pilot", "source": {"sourceTitle": "Algebra"}, "cases": [{
            "id": "case-1", "subject": "math", "sourcePage": 1,
            "source": {"url": "https://openstax.org/details/books/elementary-algebra-2e", "license": "CC BY 4.0",
                       "version": "2e", "contentSha256": file_hash(pdf), "sourceAssetId": "openstax-elementary-algebra-2e"},
            "sourceLocator": {"quote": "Verified source quote for the test."},
            "input": {"ocrText": "Verified source quote"}, "expected": {"text": "Verified source quote for the test."},
            "actual": {"text": "Verified source quote"},
        }]}, {"actorId": "importer", "identityVerified": False})
        case = store.list_cases(package["packageId"])["items"][0]
        reviewer = ScriptedReviewer({"Verified source quote": {"verdict": "concern", "summary": "输出缺少结尾",
                                                               "issues": [{"code": "missing_condition", "severity": "minor",
                                                                           "message": "缺少 for the test", "sourcePage": 1}]}})
        service = self._service(reviewer, rate=0.0, quality_review=quality_review)
        # When the teacher requests an AI opinion and the job runs
        opinion = service.request_case_opinion(case["caseId"], expected_record_version=case["recordVersion"])
        self._run_job(service, str(opinion["jobId"]), handler="quality_review.ai_opinion")
        # Then the opinion is visible, the source text reached the reviewer, and no human decision was made
        current = service.case_opinion(case["caseId"])
        self.assertEqual((current["verdict"], current["stale"]), ("concern", False))
        self.assertIn("Verified source quote for the test.", reviewer.calls[0]["prompt"])
        stored = quality_review.get_case(case["caseId"])
        self.assertEqual((stored["decisions"], stored["counted"]), ([], False))
        self.assertEqual(stored["reviewStatus"], case["reviewStatus"])

    def test_user_calls_the_ai_review_api_in_protected_mode_then_only_teachers_reach_it(self) -> None:
        # Given protected-mode sessions for a teacher and a student
        self.enterContext(environment({"AUTH_MODE": "protected", "AUTH_COOKIE_SECURE": "1",
                                       "TEACHER_BOOTSTRAP_SECRET": "ai-review-engineering-test-secret-" * 2,
                                       "CORS_ORIGINS": "https://testserver"}))
        auth = AuthSessionStore(engine=self.app_store.engine)
        app = create_app()
        app.state.auth_session_store = auth
        reviewer = ScriptedReviewer({})
        app.include_router(build_courseware_router(self.courseware, ai_review_service=self._service(reviewer, rate=0.0)))
        client = self.enterContext(TestClient(app, base_url="https://testserver"))
        path = f"/api/courseware/{self.courseware_id}/ai-review"
        teacher_token, _ = auth.create_session(role="teacher", learner_id=None)
        student_token, _ = auth.create_session(role="student", learner_id="ai-review-learner")
        version = self._document()["recordVersion"]
        # When a student reads or starts AI pre-review, Then both are refused and no model is called
        client.cookies.set(COOKIE_NAME, student_token)
        self.assertEqual(client.get(path).status_code, 403)
        self.assertEqual(client.post(path, json={"expectedRecordVersion": version}).status_code, 403)
        # When the teacher starts it, Then the typed estimate is returned without approving anything
        client.cookies.set(COOKIE_NAME, teacher_token)
        started = client.post(path, json={"expectedRecordVersion": version})
        self.assertEqual(started.status_code, 202, started.text)
        self.assertEqual(started.json()["pagesToReview"], 3)
        self.assertFalse(client.get(path).json()["countsAsHumanReview"])
        self.assertEqual(reviewer.calls, [])
