"""User acceptance for automatic routing and bounded, recoverable course preparation."""

from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from application.job_worker import JobCancelled
from application.services.chapter_courses import ChapterCourseService
from application.services.material_courses import MaterialCourseService
from application.services.textbook_processing import TextbookProcessingService
from application.textbook_jobs import build_textbook_registry
from domain.chapters.material import chapter_ranges, classify_material, headings
from infrastructure.files.upload_registry import UploadRegistry
from infrastructure.runtime.ocr_runtime import runtime
from persistence.app_store import AppStore
from persistence.job_store import JobStore
from routers.chapter_routes import build_chapter_router
from tests.postgres_test_support import PostgresTestCase


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
        self.assertEqual(chapter_ranges([], 120, "Original")[0], {
            "title": "Original", "pageStart": 1, "pageEnd": 80, "boundaryDetected": False})
        self.assertEqual(chapter_ranges([{"title": "Invalid", "page": 500}], 2, "Original")[0]["boundaryDetected"], False)

    def test_user_uploads_seven_chapters_then_only_first_five_use_the_sixth_boundary(self):
        result = chapter_ranges([{"title": f"Unit {i}", "page": 2 * i - 1} for i in range(1, 8)], 14, "Book")
        self.assertEqual([(r["pageStart"], r["pageEnd"]) for r in result], [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10)])

    def test_user_opens_a_book_with_units_and_nested_chapters_then_actual_chapters_define_the_first_five(self):
        import io

        from pypdf import PdfReader
        writer = PdfWriter()
        for _ in range(20):
            writer.add_blank_page(width=200, height=300)
        for unit in range(2):
            parent = writer.add_outline_item(f"Unit {unit + 1} Writing", unit * 10)
            for chapter in range(3):
                writer.add_outline_item(f"Chapter {unit * 3 + chapter + 1} Reading", unit * 10 + chapter * 3, parent=parent)
        stream = io.BytesIO()
        writer.write(stream)
        stream.seek(0)
        starts = MaterialCourseService._outline(PdfReader(stream))
        ranges = chapter_ranges(starts, 20, "Book")
        self.assertEqual([item["title"] for item in ranges], [f"Chapter {i} Reading" for i in range(1, 6)])
        self.assertEqual(ranges[-1]["pageEnd"], 16)


class AutomaticCourseAcceptanceTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        self.store = AppStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.store.close)
        self.jobs = JobStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.jobs.close)
        directory = self.store.upload_root / "automatic-book"
        directory.mkdir(parents=True)
        writer = PdfWriter()
        for _ in range(14):
            writer.add_blank_page(width=200, height=300)
        for i in range(1, 8):
            parent = writer.add_outline_item(f"Unit {i} Our school", 2 * i - 2)
            writer.add_outline_item("第一节 子章节", 2 * i - 1, parent=parent)
        writer.write(directory / "source.pdf")
        self.store.save_job({"uploadId": "automatic-book", "filename": "English textbook.pdf",
            "contentType": "application/pdf", "size": (directory / "source.pdf").stat().st_size,
            "chunkSize": 1000, "totalChunks": 1, "directory": directory, "status": "complete",
            "sourceText": "\n".join(f"<!-- page {i} -->\nWe went to school. We read a book." for i in range(1, 15))})
        self.chapters = ChapterCourseService(self.store, self.jobs)
        self.service = MaterialCourseService(self.chapters, None)

    def test_user_makes_courses_without_options_then_five_source_bound_drafts_are_queued_and_retry_reuses_them(self):
        # Given a real seven-chapter PDF with page-addressed OCR fixture text
        result = self.service.run("automatic-book", lambda: False)
        # When course preparation is repeated after a transport interruption
        repeated = self.service.run("automatic-book", lambda: False)
        # Then the same first five chapters are returned, originals and review gates remain
        self.assertEqual(result["chapters"], repeated["chapters"])
        chapters = self.store.list_chapters()
        self.assertEqual(len(chapters), 5)
        self.assertEqual(self.store.list_imports()[0]["materialKind"], "textbook")
        self.assertEqual(len(self.jobs.list_jobs()), 5)
        self.assertEqual(sorted(c["sourceRevisions"][0]["pageEnd"] for c in chapters), [2, 4, 6, 8, 10])
        for chapter in chapters:
            self.assertEqual(chapter["subject"], "english")
            self.assertEqual(chapter["teachingMode"], "tutorial")
            self.assertEqual(chapter["currentLessonIds"], [])
            source = chapter["sourceRevisions"][0]
            self.assertTrue(source["sourceFileSha256"])
            self.assertIn("source_license_missing", [issue["code"] for issue in source["issues"]])
            queued = self.chapters.enqueue_ai_generation(chapter["chapterId"], expected_record_version=chapter["recordVersion"])
            self.assertEqual(queued["status"], "queued")
            with self.assertRaises(ValueError):
                self.chapters.publish(chapter["chapterId"])
        self.assertTrue((self.store.upload_root / "automatic-book" / "source.pdf").is_file())

    def test_user_requests_automatic_preparation_then_no_options_are_required_and_requests_are_idempotent(self):
        app = FastAPI()
        app.include_router(build_chapter_router(self.chapters))
        with TestClient(app) as client:
            first = client.post("/api/chapters/from-upload/automatic-book")
            second = client.post("/api/chapters/from-upload/automatic-book")
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(first.json()["jobId"], second.json()["jobId"])
        self.assertEqual(first.json()["jobType"], "material.courses.create")

    def test_user_uploads_a_textbook_then_ocr_is_saved_without_generating_exam_questions(self):
        # Given uploaded PDF chunks and supplied OCR source, with no model required
        job = self.store.load_job("automatic-book")
        assert job is not None
        original = (job["directory"] / "source.pdf").read_bytes()
        (job["directory"] / "chunk-000000.part").write_bytes(original)
        job.update(status="uploading", chunkSize=len(original), sourceText="<!-- page 1 -->\nEnglish textbook\nUnit 1 Our school\nWe went to school.")
        self.store.save_job(job)
        registry = UploadRegistry(store=self.store, lesson_store={}, default_guide_cards=[], pdf_tail_check_bytes=65536)
        processing = TextbookProcessingService(store=self.store, upload_registry=registry, ocr_runtime=runtime)
        # When the unified upload requests automatic document routing
        result = build_textbook_registry(processing).get("textbook.upload.complete")(
            {"uploadId": "automatic-book", "autoDetect": True, "generateFullPaper": True}, lambda: False,
        )
        # Then the result is source-only and the library retains the original upload
        self.assertEqual(result["materialKind"], "textbook")
        self.assertEqual(result["questionPayloads"], [])
        self.assertNotIn("modelRun", result)
        self.assertEqual(self.store.list_imports()[0]["materialKind"], "textbook")
        self.assertEqual((job["directory"] / "source.pdf").read_bytes(), original)

    def test_user_uploads_a_textbook_image_then_original_and_ocr_are_retained_for_automatic_courses(self):
        registry = UploadRegistry(store=self.store, lesson_store={}, default_guide_cards=[], pdf_tail_check_bytes=65536)
        processing = TextbookProcessingService(store=self.store, upload_registry=registry, ocr_runtime=runtime)
        original = b"original fixture image bytes"
        result = processing.preserve_material_page(filename="English textbook.png", content_type="image/png",
            content=original, source="Unit 1 Hello\nWe read a book.", ocr_run={"provider": "fixture"},
            detection={"kind": "textbook", "reason": "章节标题"})
        job = self.store.load_job(result["uploadId"])
        assert job is not None
        self.assertEqual((job["directory"] / "original.png").read_bytes(), original)
        courses = self.service.run(result["uploadId"], lambda: False)
        self.assertEqual(len(courses["chapters"]), 1)
        chapter = self.store.load_chapter(courses["chapters"][0]["chapterId"])
        assert chapter is not None
        self.assertEqual(chapter["subject"], "english")
        self.assertEqual(chapter["sourceRevisions"][0]["pages"][0]["text"], "Unit 1 Hello\nWe read a book.")

    def test_user_cancels_course_preparation_then_no_chapter_is_created_and_source_is_retained(self):
        with self.assertRaises(JobCancelled):
            self.service.run("automatic-book", lambda: True)
        self.assertEqual(self.store.list_chapters(), [])
        self.assertIsNotNone(self.store.load_job("automatic-book"))

    def test_user_deletes_a_course_then_it_disappears_but_sources_and_queued_job_history_remain(self):
        result = self.service.run("automatic-book", lambda: False)
        chapter_id = result["chapters"][0]["chapterId"]
        job = self.jobs.latest_for_payload("chapter.lesson.generate", "chapterId", chapter_id)
        app = FastAPI()
        app.include_router(build_chapter_router(self.chapters))
        with TestClient(app) as client:
            response = client.delete(f"/api/chapters/{chapter_id}")
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(chapter_id, [c["chapterId"] for c in client.get("/api/chapters").json()["items"]])
            self.assertEqual(client.get(f"/api/chapters/{chapter_id}").status_code, 404)
            self.assertEqual(client.delete(f"/api/chapters/{chapter_id}").status_code, 404)
        archived = self.store.load_chapter(chapter_id)
        self.assertTrue(archived["deletedAt"])
        self.assertTrue(archived["sourceRevisions"])
        self.assertEqual(self.jobs.get_job(job["jobId"])["status"], "cancelled")
        self.assertTrue((self.store.upload_root / "automatic-book" / "source.pdf").is_file())

    def test_user_views_legacy_exam_upload_then_existing_title_evidence_classifies_it_as_a_paper(self):
        job = self.store.load_job("automatic-book")
        job["filename"] = "初中数学浙江中考数学真题.pdf"
        job["result"] = {"extraction": {"questionCount": 24}}
        self.store.save_job(job)
        self.assertEqual(self.store.list_imports()[0]["materialKind"], "paper")

    def test_user_makes_courses_with_mineru_selected_then_the_task_uses_auto_without_changing_upload_selection(self):
        with runtime.use_selection("mineru"):
            job = self.service.enqueue("automatic-book")
            self.assertEqual(job["payload"]["runtimeSnapshot"]["ocr"]["provider"], "auto")
            self.assertEqual(runtime.selection.provider, "mineru")

    def test_user_deletes_an_upload_during_preparation_then_a_stale_ocr_save_cannot_restore_it(self):
        job = self.store.load_job("automatic-book")
        self.assertTrue(self.store.soft_delete_import("automatic-book"))
        self.store.save_job(job)
        self.assertEqual(self.store.load_job("automatic-book")["status"], "deleted")
        self.assertEqual(self.store.list_imports(), [])
        self.assertTrue((job["directory"] / "source.pdf").exists())
