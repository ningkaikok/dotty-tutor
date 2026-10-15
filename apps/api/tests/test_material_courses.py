"""User acceptance for automatic routing and bounded, recoverable course preparation."""

from __future__ import annotations

import time
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from application.job_worker import JobExecutionContext
from application.services.chapter_courses import ChapterCourseService
from application.services.material_courses import MaterialCourseService
from domain.chapters.material import classify_material, headings
from domain.contracts.courseware import CoursewareSourcePreparationRequest
from infrastructure.runtime.ocr_runtime import OcrRuntime, OcrSelection
from persistence.app_store import AppStore
from persistence.job_store import JobStore
from routers.chapter_routes import build_chapter_router
from tests.postgres_test_support import PostgresTestCase
from tests.test_chapter_real_sources import _make_pdf


class MaterialRecognitionTests(unittest.TestCase):
    def test_user_uploads_material_then_exam_evidence_and_book_headings_route_automatically(self):
        self.assertEqual(classify_material("写作教程.pdf", "1. Introduction")["kind"], "textbook")
        self.assertEqual(classify_material("测试卷.pdf", "Unit 1 Numbers")['kind'], "paper")
        self.assertEqual(classify_material("English textbook.pdf", "1. What is it?")['kind'], "textbook")
        self.assertEqual(classify_material("page.png", "Unit 2 Our school")['kind'], "textbook")
        self.assertEqual(classify_material("page.png", "1. Solve x+1=2")['kind'], "paper")
        self.assertEqual(classify_material("page.png", "A short passage without headings.")['kind'], "unknown")

    def test_user_uploads_a_book_then_contents_and_repeated_headers_are_not_extra_chapters(self):
        pages = [{"page": 1, "text": "Contents\nUnit 1 Hello\nUnit 2 School"},
                 {"page": 2, "text": "Unit 1 Hello\nWe say hello."},
                 {"page": 3, "text": "Unit 1 Practice\nMore practice."},
                 {"page": 4, "text": "Unit 2 School\nWe go to school."}]
        self.assertEqual(headings(pages), [{"title": "Unit 1 Hello", "page": 2}, {"title": "Unit 2 School", "page": 4}])

class CoursewareSourcePreparationAcceptanceTests(PostgresTestCase):
    """User-facing classification and source-preparation behavior on PostgreSQL."""

    def setUp(self):
        super().setUp()
        self.store = AppStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.store.close)
        self.jobs = JobStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.jobs.close)
        directory = self.store.upload_root / "source-book"
        directory.mkdir(parents=True)
        writer = PdfWriter()
        for _ in range(30):
            writer.add_blank_page(width=200, height=300)
        writer.write(directory / "source.pdf")
        source_text = "\n".join(
            f"<!-- page {number} -->\nThe Digital World: section {number}. We read critically."
            for number in range(23, 31)
        )
        self.store.save_job({"uploadId": "source-book", "filename": "English textbook.pdf",
            "contentType": "application/pdf", "size": (directory / "source.pdf").stat().st_size,
            "chunkSize": 1000, "totalChunks": 1, "directory": directory, "status": "complete",
            "sourceText": source_text,
            "result": {"materialKind": "textbook", "extraction": {"pageCount": 30}}})
        self.chapters = ChapterCourseService(self.store, self.jobs)
        self.service = MaterialCourseService(self.chapters, None)

    def _request(self, **overrides):
        values = {
            "subject": "english", "pageStart": 23, "pageEnd": 25,
            "sourceTitle": "Writing Guide with Handbook",
            "license": "CC BY-NC-SA 4.0",
            "attributionText": "Access for free at openstax.org.",
            "distributionScope": "classroom",
        }
        values.update(overrides)
        return CoursewareSourcePreparationRequest.model_validate(values)

    def _execute(self, job):
        claim = self.jobs.claim_next("source-worker", lease_seconds=600)
        self.assertIsNotNone(claim)
        assert claim is not None
        context = JobExecutionContext(
            claim["jobId"], claim["leaseOwner"], claim["attemptCount"], claim["leaseExpiresAt"],
        )
        return self.service.run(claim["payload"], lambda: False, context)

    def test_user_confirms_unknown_material_as_textbook_then_one_source_only_chapter_is_prepared_idempotently(self):
        # Given a completed upload whose OCR is page-addressed and whose type is explicit
        upload = self.store.load_job("source-book")
        assert upload is not None
        upload["result"]["materialKind"] = "unknown"
        self.store.save_job(upload)
        # When the teacher first omits confirmation, the source is not silently reclassified
        with self.assertRaisesRegex(ValueError, "尚未确认"):
            self.service.enqueue("source-book", self._request())
        request = self._request(confirmedMaterialKind="textbook")
        first = self.service.enqueue("source-book", request, idempotency_key="prepare-opening")
        repeated = self.service.enqueue("source-book", request, idempotency_key="prepare-opening")
        self.assertEqual(first["jobId"], repeated["jobId"])
        # Then the worker creates one reviewable source chapter and no legacy lesson job
        execution = self._execute(first)
        result = execution.value
        self.assertEqual(result["materialKind"], "textbook")
        self.assertEqual(len(result["chapters"]), 1)
        chapter_result = result["chapters"][0]
        self.assertEqual((chapter_result["pageStart"], chapter_result["pageEnd"]), (23, 25))
        chapter = self.store.load_chapter(chapter_result["chapterId"])
        self.assertEqual(chapter["teachingMode"], "tutorial")
        self.assertEqual(chapter["currentLessonIds"], [])
        source = chapter["sourceRevisions"][-1]
        self.assertEqual(source["distributionScope"], "classroom")
        self.assertTrue(source["sourceFileSha256"])
        self.assertTrue((self.store.upload_root / "source-book" / "source.pdf").is_file())
        self.assertEqual([item["jobType"] for item in self.jobs.list_jobs()], ["courseware.source.prepare"])

    def test_user_routes_a_paper_upload_then_the_textbook_courseware_path_rejects_it(self):
        # Given a completed upload already classified by the existing paper route
        upload = self.store.load_job("source-book")
        assert upload is not None
        upload["result"]["materialKind"] = "paper"
        upload["filename"] = "exam.pdf"
        self.store.save_job(upload)
        # When a teacher requests textbook preparation
        with self.assertRaisesRegex(ValueError, "试卷"):
            self.service.enqueue("source-book", self._request())
        # Then no tutorial chapter or courseware preparation job is created
        self.assertEqual(self.store.list_chapters(), [])
        self.assertEqual(self.jobs.list_jobs(), [])

    def test_user_uploads_aggregate_preview_then_selected_pdf_pages_are_prepared_without_overwriting_ocr(self):
        # Given a real PDF and a legacy aggregate OCR preview with no page markers
        upload = self.store.load_job("source-book")
        assert upload is not None
        _make_pdf(upload["directory"] / "source.pdf", "Read critically and cite evidence from the source.")
        upload["sourceText"] = "Aggregate MinerU preview without physical-page boundaries."
        upload["result"]["extraction"]["pageCount"] = 1
        self.store.save_job(upload)
        ocr = OcrRuntime()
        ocr.selection = OcrSelection("pypdf")
        self.service = MaterialCourseService(self.chapters, ocr)
        # When the teacher selects the original PDF page through the normal queue
        queued = self.service.enqueue("source-book", self._request(pageStart=1, pageEnd=1))
        result = self._execute(queued).value
        # Then the source has the correct physical page and original OCR stays intact
        chapter = self.store.load_chapter(result["chapters"][0]["chapterId"])
        self.assertEqual(chapter["sourceRevisions"][-1]["pages"][0]["page"], 1)
        self.assertIn("Read critically", chapter["sourceRevisions"][-1]["pages"][0]["text"])
        stored = self.store.load_job("source-book")
        self.assertEqual(stored["sourceText"], upload["sourceText"])
        self.assertEqual(stored["result"]["coursewareSource"]["pages"][0]["ocrRun"]["provider"], "pypdf")

    def test_user_cancels_source_preparation_then_a_late_worker_cannot_save_ocr_pages(self):
        # Given a claimed source task that the user cancels
        queued = self.service.enqueue("source-book", self._request())
        claim = self.jobs.claim_next("late-worker", lease_seconds=600)
        assert claim is not None
        self.jobs.request_cancel(queued["jobId"])
        context = JobExecutionContext(claim["jobId"], claim["leaseOwner"], claim["attemptCount"], claim["leaseExpiresAt"])
        # When the stale execution tries to checkpoint a page
        with self.assertRaisesRegex(ValueError, "取消"):
            self.store.checkpoint_courseware_source(context=context, upload_id="source-book",
                source_text_sha256="unused", source_file_sha256="unused",
                page={"page": 23, "text": "Late OCR"}, now=time.time())
        # Then no new OCR snapshot or chapter is written
        self.assertNotIn("coursewareSource", self.store.load_job("source-book")["result"])

    def test_user_prepares_an_unreadable_pdf_then_failure_identifies_the_physical_page_without_creating_a_chapter(self):
        # Given an uploaded PDF whose original bytes cannot be parsed and no reusable page OCR
        upload = self.store.load_job("source-book")
        assert upload is not None
        (upload["directory"] / "source.pdf").write_bytes(b"%PDF-1.7\ninvalid physical page\n%%EOF")
        upload["sourceText"] = "Aggregate preview without page boundaries."
        self.store.save_job(upload)
        ocr = OcrRuntime()
        ocr.selection = OcrSelection("pypdf")
        self.service = MaterialCourseService(self.chapters, ocr)
        job = self.service.enqueue("source-book", self._request(pageStart=1, pageEnd=1))
        # When source preparation fails at the PDF/OCR boundary
        with self.assertRaisesRegex(Exception, "教材第 1 页"):
            self._execute(job)
        # Then the teacher can locate that page and the original evidence stays untouched
        progress = self.jobs.get_job(job["jobId"])
        self.assertIn("第 1 页", progress["message"])
        self.assertEqual(self.store.list_chapters(), [])
        self.assertEqual(self.store.load_job("source-book")["sourceText"], upload["sourceText"])
        self.assertEqual(self.store.list_chapters(), [])

    def test_user_selects_a_source_range_then_missing_pages_and_unregistered_profiles_fail_closed(self):
        # Given OCR only covers physical pages 23 through 30
        with self.assertRaisesRegex(ValueError, "缺少所选页"):
            self.service.enqueue("source-book", self._request(pageStart=22, pageEnd=25))
        with self.assertRaisesRegex(ValueError, "尚未配置"):
            self.service.enqueue("source-book", self._request(subject="unknown_subject"))
        self.assertEqual(self.jobs.list_jobs(), [])

    def test_user_requests_source_preparation_over_http_then_receives_a_pollable_job(self):
        app = FastAPI()
        app.include_router(build_chapter_router(self.chapters, source_preparation_service=self.service))
        with TestClient(app) as client:
            first = client.post(
                "/api/chapters/from-upload/source-book",
                json=self._request().model_dump(mode="json"),
                headers={"Idempotency-Key": "http-prepare-opening"},
            )
            second = client.post(
                "/api/chapters/from-upload/source-book",
                json=self._request().model_dump(mode="json"),
                headers={"Idempotency-Key": "http-prepare-opening"},
            )
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(first.json()["jobId"], second.json()["jobId"])
        self.assertEqual(first.json()["jobType"], "courseware.source.prepare")
