"""Given/When/Then PostgreSQL acceptance for resumable whole-book production."""

from __future__ import annotations

import json
import re
import shutil
import unittest
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from application.chapter_jobs import build_chapter_registry
from application.courseware_jobs import build_courseware_registry
from application.errors import AppError
from application.job_registry import merge_registries
from application.job_worker import JobWorker
from application.services.chapter_courses import ChapterCourseService
from application.services.chapter_source_preview import file_hash
from application.services.courseware import CoursewareService
from application.services.courseware_ai_review import CoursewareAiReviewService
from application.services.courseware_automation import CoursewareAutomationService
from application.services.material_courses import MaterialCourseService
from application.services.whole_book import WholeBookService
from application.whole_book_jobs import (
    add_whole_book_completion_hooks,
    build_whole_book_registry,
)
from domain.contracts.courseware import (
    CoursewarePageContent,
    CoursewareSourcePreparationRequest,
)
from domain.contracts.whole_book import WholeBookStartRequest
from infrastructure.files.file_storage import FileStorageError
from persistence.ai_review_store import AiReviewStore
from persistence.app_store import AppStore
from persistence.courseware_store import CoursewareStore
from persistence.job_store import JobStore
from routers.whole_book_routes import build_whole_book_router
from tests.postgres_test_support import PostgresTestCase
from tests.test_courseware_ai_review_postgres import ScriptedReviewer


class WholeBookRuntime:
    """Deterministic stand-in for the external model boundary."""

    def __init__(
        self, *, fail_plan_at_page: int | None = None, skip_instructional_pages: bool = False,
    ) -> None:
        self.fail_plan_at_page = fail_plan_at_page
        self.skip_instructional_pages = skip_instructional_pages

    @staticmethod
    def _line_json(prompt: str, label: str, ending: str) -> dict[str, Any]:
        match = re.search(rf"{re.escape(label)} (.+?)\n{re.escape(ending)}", prompt, re.S)
        if not match:
            raise AssertionError(f"prompt is missing {label}")
        return json.loads(match.group(1).strip())

    def generate_json(self, prompt: str, schema: dict[str, Any], *, task: str, **kwargs: Any):
        del schema, kwargs
        if task == "courseware_plan_generation":
            match = re.search(
                r"Source pages and exact sentence/region identifiers: (.+?)\n\nGround every",
                prompt,
                re.S,
            )
            if not match:
                raise AssertionError("plan prompt is missing its frozen physical pages")
            source = json.loads(match.group(1))
            pages = source["pages"]
            first_page = int(pages[0]["page"])
            if self.fail_plan_at_page == first_page:
                raise ValueError("intentional second-batch conflict")
            dispositions: list[dict[str, Any]] = []
            covered_refs: list[dict[str, Any]] = []
            for page in pages:
                number = int(page["page"])
                text = str(page.get("text") or "")
                if self.skip_instructional_pages:
                    dispositions.append({"page": number, "disposition": "skipped", "reason": "cover_or_title", "note": "教学页被错误标记为封面"})
                elif number == 1 and "textbook" in text.lower():
                    dispositions.append({"page": number, "disposition": "skipped", "reason": "cover_or_title", "note": "教材封面标题页"})
                elif number == 2 and not text.strip():
                    dispositions.append({"page": number, "disposition": "skipped", "reason": "blank_page", "note": "原PDF渲染核验为空白页"})
                elif number == 3 and text.lower().startswith("contents"):
                    dispositions.append({"page": number, "disposition": "skipped", "reason": "table_of_contents", "note": "目录页用于识别章节范围"})
                else:
                    dispositions.append({"page": number, "disposition": "covered"})
                    sentence = page["sentences"][0]
                    covered_refs.append({
                        "sourceRevisionId": source["sourceRevisionId"],
                        "page": number,
                        "sentenceIds": [sentence["sentenceId"]],
                    })
            objectives = [{
                "objectiveId": "book-reading",
                "description": "Explain the key ideas from the selected textbook pages.",
                "sourceRefs": covered_refs[:20],
            }]
            outlines = []
            for offset in range(0, len(covered_refs), 3):
                refs = covered_refs[offset:offset + 3]
                outlines.append({
                    "pageId": f"source-pages-{refs[0]['page']}-{refs[-1]['page']}",
                    "title": f"Read pages {refs[0]['page']}–{refs[-1]['page']}",
                    "pageKind": "explanation", "layout": "text_focus",
                    "objectiveIds": ["book-reading"], "sourceRefs": refs,
                    "estimatedMinutes": 1,
                })
            sections = []
            sessions = []
            if outlines:
                minutes = max(10, len(outlines))
                for item in outlines:
                    item["estimatedMinutes"] = 1
                sections = [{
                    "sectionId": "read-source-pages", "title": "Read the source pages",
                    "purpose": "Build a source-grounded lesson.", "estimatedMinutes": minutes,
                    "pages": outlines,
                }]
                sessions = [{
                    "sessionId": "textbook-session", "title": "Textbook lesson",
                    "durationMinutes": minutes, "sections": sections,
                }]
            return {
                "objectives": objectives if outlines else [],
                "sessions": sessions,
                "coverageMap": {"book-reading": covered_refs[:20]} if outlines else {},
                "pageDispositions": dispositions,
            }, {"provider": "whole-book-test", "model": "deterministic", "durationMs": 1}

        if task == "courseware_page_generation":
            outline_match = re.search(r"Page outline: (.+?)\nObjectives:", prompt, re.S)
            if not outline_match:
                raise AssertionError("page prompt is missing its frozen outline")
            outline = json.loads(outline_match.group(1))
            refs = outline["sourceRefs"]
            return {
                "blocks": [{
                    "kind": "text", "blockId": f"{outline['pageId']}-lesson-text",
                    "text": f"Explain the source ideas on physical pages {', '.join(str(item['page']) for item in refs)}.",
                    "sourceRefs": refs, "authorship": "ai_supplement",
                }],
                "teacherNotes": "Use the cited source pages during the lesson.",
                "revealSteps": [],
            }, {"provider": "whole-book-test", "model": "deterministic", "durationMs": 1}
        raise AssertionError(f"unexpected generation task: {task}")


class FailTwiceFileAssets:
    """Simulate a transient remote restore failure at the file-storage edge."""

    enabled = True

    def __init__(self, cached_source: Any) -> None:
        self.failures_remaining = 2
        self.cached_source = cached_source

    def restore_upload_file(self, _upload_id: str, path: Any) -> None:
        if path.is_file():
            return
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise FileStorageError("temporary file storage outage")
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.cached_source, path)


    def close(self) -> None:
        """Match the storage adapter cleanup interface without owning resources."""
        return None


class ManifestOnlyFileAssets:
    """Expose the immutable remote manifest while refusing any byte download."""

    enabled = True

    def __init__(self, source_sha256: str) -> None:
        self.source_sha256 = source_sha256
        self.restore_calls = 0
        self.manifest_calls = 0

    def get_upload_file_manifest(self, _upload_id: str, relative_path: str) -> dict[str, Any] | None:
        self.manifest_calls += 1
        return {"sha256": self.source_sha256, "size": 123} if relative_path == "source.pdf" else None

    def restore_upload_file(self, _upload_id: str, _path: Any) -> None:
        self.restore_calls += 1
        raise AssertionError("read-only status must not download source bytes")


    def close(self) -> None:
        """Match the storage adapter cleanup interface without owning resources."""
        return None


class WholeBookAutomationPostgresAcceptanceTests(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.app_store = AppStore(database_url=self.database_url, data_root=self.data_root)
        self.jobs = JobStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.app_store.close)
        self.addCleanup(self.jobs.close)
        self.courseware_store = CoursewareStore(engine=self.app_store.engine)
        self.chapters = ChapterCourseService(self.app_store, self.jobs)
        self.courseware = CoursewareService(
            store=self.courseware_store,
            chapters=self.chapters,
            jobs=self.jobs,
            runtime=WholeBookRuntime(),
        )
        self.material_courses = MaterialCourseService(self.chapters, None, courseware=self.courseware)
        self.ai_review = CoursewareAiReviewService(
            courseware=self.courseware,
            store=AiReviewStore(engine=self.app_store.engine),
            jobs=self.jobs,
            runtime=ScriptedReviewer({}),
            sampling_rate=0,
        )
        self.courseware.automation = CoursewareAutomationService(
            courseware=self.courseware, reviewer=self.ai_review,
        )
        self.whole_book = WholeBookService(
            store=self.app_store,
            jobs=self.jobs,
            material_courses=self.material_courses,
            courseware=self.courseware,
        )
        chapter_registry = build_chapter_registry(self.chapters, self.material_courses)
        courseware_registry = build_courseware_registry(self.courseware)
        self.registry = merge_registries(
            add_whole_book_completion_hooks(chapter_registry, self.whole_book),
            add_whole_book_completion_hooks(courseware_registry, self.whole_book),
            build_whole_book_registry(self.whole_book),
        )

    def _upload(
        self, upload_id: str, *, page_count: int = 35, front_matter: bool = True,
        omit_ocr_pages: set[int] | None = None, formula_page: int | None = None,
    ) -> None:
        directory = self.app_store.upload_root / upload_id
        directory.mkdir(parents=True)
        writer = PdfWriter()
        for number in range(1, page_count + 1):
            page = writer.add_blank_page(width=595, height=842)
            if number == formula_page:
                font = writer._add_object(DictionaryObject({
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }))
                page[NameObject("/Resources")] = DictionaryObject({
                    NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
                })
                stream = StreamObject()
                stream.set_data(b"BT /F1 10 Tf 72 770 Td (x = 2) Tj ET")
                page[NameObject("/Contents")] = writer._add_object(stream)
        with (directory / "source.pdf").open("wb") as output:
            writer.write(output)
        pages = []
        for number in range(1, page_count + 1):
            if omit_ocr_pages and number in omit_ocr_pages:
                continue
            if front_matter and number == 1:
                text = "English textbook, Grade 7"
            elif front_matter and number == 2:
                text = ""
            elif front_matter and number == 3:
                text = "Contents\nUnit 1 Reading\nUnit 2 Writing"
            elif number == 31:
                text = "Chapter 2: Evidence and explanation."
            else:
                text = f"This is instructional textbook content on physical page {number}."
            pages.append(f"<!-- page {number} -->\n{text}")
        source_text = "\n".join(pages)
        path = directory / "source.pdf"
        self.app_store.save_job({
            "uploadId": upload_id,
            "filename": "English textbook.pdf",
            "contentType": "application/pdf",
            "size": path.stat().st_size,
            "chunkSize": 1024,
            "totalChunks": 1,
            "directory": directory,
            "status": "complete",
            "sourceText": source_text,
            "result": {"materialKind": "textbook", "extraction": {"pageCount": page_count}},
        })

    @staticmethod
    def _request() -> WholeBookStartRequest:
        return WholeBookStartRequest(mode="whole_book", subject="english")

    def _worker(self, name: str = "whole-book-worker") -> JobWorker:
        return JobWorker(self.jobs, self.registry, worker_id=name, poll_interval=0)

    def _drain(self, worker: JobWorker, *, limit: int = 160) -> list[dict[str, Any]]:
        results = []
        for _ in range(limit):
            result = worker.run_once()
            if result is None:
                return results
            results.append(result)
        self.fail("whole-book Worker did not drain its bounded queue")
        return results

    def test_user_starts_773_and_1000_page_books_but_rejects_1001_without_running_models(self) -> None:
        # Given complete textbook PDFs at the supported 773-page and 1000-page boundaries
        for page_count, expected_batch_count in ((773, 26), (1000, 34)):
            upload_id = f"whole-book-boundary-{page_count}"
            self._upload(upload_id, page_count=page_count, front_matter=False)

            # When the teacher starts whole-book production without running the Worker
            started = self.whole_book.start(upload_id, self._request(), actor_id="teacher-a")
            progress = self.whole_book.get_run(started.runId)

            # Then persisted batches cover the original page range once, with no model work started
            self.assertEqual(progress.totalPages, page_count)
            self.assertEqual(progress.batchCount, expected_batch_count)
            page_numbers = [
                number
                for batch in progress.batches
                for number in range(batch.pageStart, batch.pageEnd + 1)
            ]
            self.assertEqual(page_numbers, list(range(1, page_count + 1)))
            self.assertTrue(all(batch.pageEnd - batch.pageStart < 30 for batch in progress.batches))

        self._upload("whole-book-boundary-1001", page_count=1001, front_matter=False)
        with self.assertRaisesRegex(ValueError, "1–1000"):
            self.whole_book.start("whole-book-boundary-1001", self._request(), actor_id="teacher-a")

    @unittest.skipUnless(shutil.which("pdftoppm"), "blank-page verification requires Poppler")
    def test_user_starts_a_textbook_then_two_batches_publish_and_non_teaching_pages_are_audited(self) -> None:
        # Given a 35-page textbook whose OCR includes a cover, a blank scan, and a table of contents
        self._upload("whole-book-complete", omit_ocr_pages={2})
        # When the teacher explicitly chooses whole-book production and retries the request
        first = self.whole_book.start("whole-book-complete", self._request(), actor_id="teacher-a")
        repeated = self.whole_book.start("whole-book-complete", self._request(), actor_id="teacher-a")
        self.assertEqual(first.runId, repeated.runId)
        self._drain(self._worker())
        # Then the original physical range completes in two bounded batches without reselecting pages
        completed = self.whole_book.get_run(first.runId)
        self.assertEqual(completed.status, "completed", completed.model_dump())
        self.assertEqual([(item.pageStart, item.pageEnd) for item in completed.batches], [(1, 30), (31, 35)])
        self.assertEqual((completed.totalPages, completed.completedPages, completed.completedBatches), (35, 35, 2))
        self.assertEqual(
            [(item["page"], item["reason"]) for item in completed.batches[0].skippedPages],
            [(1, "cover_or_title"), (2, "blank_page"), (3, "table_of_contents")],
        )
        self.assertTrue(all(item.status == "published" for item in completed.batches))
        publication_ids = [value for item in completed.batches for value in item.publicationIds]
        # Repeating the same explicit request returns the existing publication set and does not replace it.
        again = self.whole_book.start("whole-book-complete", self._request(), actor_id="teacher-a")
        self.assertEqual(again.runId, completed.runId)
        self.assertEqual(self.whole_book.get_run(again.runId).batches[0].publicationIds, completed.batches[0].publicationIds)
        self.assertTrue(publication_ids)

    def test_user_keeps_a_small_pdf_formula_page_when_ocr_misses_it_and_the_model_calls_it_blank(self) -> None:
        # Given a real A4 PDF whose OCR omitted a page containing only a small formula
        self._upload(
            "whole-book-small-formula", page_count=2, front_matter=False,
            omit_ocr_pages={2}, formula_page=2,
        )
        upload = self.app_store.load_job("whole-book-small-formula")
        assert upload is not None
        from pypdf import PdfReader

        self.assertEqual((PdfReader(upload["directory"] / "source.pdf").pages[1].extract_text() or "").strip(), "x = 2")
        started = self.whole_book.start("whole-book-small-formula", self._request(), actor_id="teacher-a")

        # When the deterministic runtime labels the OCR-empty second page blank
        self._drain(self._worker())

        # Then the text in the trusted original PDF prevents the page from being skipped
        progress = self.whole_book.get_run(started.runId)
        self.assertEqual(progress.status, "needs_attention")
        self.assertEqual(progress.completedPages, 0)
        self.assertEqual(progress.batches[0].status, "failed")
        self.assertNotIn(2, [item.page for item in progress.batches[0].skippedPages])

    def test_user_changes_the_source_then_the_old_run_stops_before_starting_another_batch(self) -> None:
        # Given a multi-batch run queued against a frozen original PDF and OCR fingerprint
        self._upload("whole-book-changed", page_count=35, front_matter=False)
        started = self.whole_book.start("whole-book-changed", self._request(), actor_id="teacher-a")
        self._worker().run_once()  # Start controller queues the first source batch.
        upload = self.app_store.load_job("whole-book-changed")
        assert upload is not None
        path = upload["directory"] / "source.pdf"
        path.write_bytes(path.read_bytes() + b" ")
        # When the source changes before the Worker prepares the first batch
        self._drain(self._worker())
        # Then the old source fingerprint is visibly stopped and a later range is never queued
        progress = self.whole_book.get_run(started.runId)
        self.assertEqual(progress.status, "source_changed")
        source_jobs = [item for item in self.jobs.list_jobs() if item["jobType"] == MaterialCourseService.JOB_TYPE]
        self.assertEqual(len(source_jobs), 1)

    def test_user_temporarily_loses_file_storage_then_the_controller_retries_without_marking_source_changed(self) -> None:
        # Given a valid book and a remote file restore service that fails once
        self._upload("whole-book-temporary-storage", page_count=31, front_matter=False)
        started = self.whole_book.start("whole-book-temporary-storage", self._request(), actor_id="teacher-a")
        upload = self.app_store.load_job("whole-book-temporary-storage")
        assert upload is not None
        local_source = upload["directory"] / "source.pdf"
        cached_source = upload["directory"] / "source.cached"
        local_source.rename(cached_source)
        self.app_store._file_assets = FailTwiceFileAssets(cached_source)
        # When the initial controller encounters the temporary outage
        first_attempt = self._worker().run_once()
        queued_run = self.jobs.get_job(started.runId)
        # Then the controller is automatically retryable, no source task was queued, and the run remains waiting
        self.assertEqual(first_attempt["status"], "queued")
        self.assertEqual(queued_run["attemptCount"], 1)
        self.assertEqual(queued_run["status"], "queued")
        waiting = self.whole_book.get_run(started.runId)
        self.assertEqual(waiting.status, "waiting_for_source")
        self.assertFalse(waiting.canPause)
        self.assertEqual(waiting.completedPages, 0)
        self.assertEqual(
            [item for item in self.jobs.list_jobs() if item["jobType"] == MaterialCourseService.JOB_TYPE],
            [],
        )

        # When the next Worker attempt can restore the unchanged source
        self._drain(self._worker())
        # Then it resumes the same full-page range and does not claim source_changed
        completed = self.whole_book.get_run(started.runId)
        self.assertEqual(completed.status, "completed", completed.model_dump())
        self.assertEqual(completed.completedPages, 31)
        self.assertEqual(completed.batches[0].status, "published")

    def test_user_reads_completed_progress_from_a_cold_cache_without_downloading_the_original_pdf(self) -> None:
        # Given a completed book and a new process cache with only a matching manifest
        self._upload("whole-book-cold-cache", page_count=5, front_matter=False)
        started = self.whole_book.start("whole-book-cold-cache", self._request(), actor_id="teacher-a")
        self._drain(self._worker())
        upload = self.app_store.load_job("whole-book-cold-cache")
        assert upload is not None
        source_path = upload["directory"] / "source.pdf"
        source_sha256 = file_hash(source_path)
        cache_copy = self.data_root / "cold-cache-source.pdf"
        source_path.rename(cache_copy)
        manifest_only = ManifestOnlyFileAssets(source_sha256)
        self.app_store._file_assets = manifest_only
        jobs_before_read = self.jobs.list_jobs()

        # When progress is read in the cold-cache process
        progress = self.whole_book.get_run(started.runId)

        # Then the stored manifest preserves completion without fetching PDF bytes or queuing work
        self.assertEqual(progress.status, "completed")
        self.assertEqual(progress.completedPages, 5)
        self.assertGreaterEqual(manifest_only.manifest_calls, 1)
        self.assertEqual(manifest_only.restore_calls, 0)
        self.assertEqual(self.jobs.list_jobs(), jobs_before_read)

    def test_user_reads_cold_cache_progress_after_ocr_changes_and_sees_source_changed(self) -> None:
        # Given a completed run whose original PDF cache is absent but whose manifest is unchanged
        self._upload("whole-book-cold-cache-ocr-change", page_count=5, front_matter=False)
        started = self.whole_book.start("whole-book-cold-cache-ocr-change", self._request(), actor_id="teacher-a")
        self._drain(self._worker())
        upload = self.app_store.load_job("whole-book-cold-cache-ocr-change")
        assert upload is not None
        source_path = upload["directory"] / "source.pdf"
        source_sha256 = file_hash(source_path)
        source_path.rename(self.data_root / "cold-cache-ocr-source.pdf")
        manifest_only = ManifestOnlyFileAssets(source_sha256)
        self.app_store._file_assets = manifest_only
        upload["sourceText"] = str(upload["sourceText"]) + "\n<!-- page 6 -->\nChanged OCR"
        self.app_store.save_job(upload)

        # When progress is read while the immutable file manifest still matches
        progress = self.whole_book.get_run(started.runId)

        # Then the OCR fingerprint still stops the old run without downloading the original PDF
        self.assertEqual(progress.status, "source_changed")
        self.assertEqual(manifest_only.restore_calls, 0)

    def test_user_starts_from_two_workers_then_both_idempotent_controllers_keep_one_batch_job(self) -> None:
        # Given concurrent start requests for the same whole-book source and settings
        self._upload("whole-book-concurrent", page_count=35, front_matter=False)
        with ThreadPoolExecutor(max_workers=2) as pool:
            starts = list(pool.map(
                lambda _: self.whole_book.start("whole-book-concurrent", self._request(), actor_id="teacher-a"),
                range(2),
            ))
        self.assertEqual(starts[0].runId, starts[1].runId)
        # When two Workers claim the start and repeated advance commands at the same time
        for command in ("a", "b"):
            self.jobs.create_job(
                "courseware.whole_book.advance", {"runId": starts[0].runId, "batchIndex": 0},
                idempotency_key=f"test-whole-book-advance:{command}", max_attempts=100,
            )
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda name: self._worker(name).run_once(), ("whole-book-a", "whole-book-b")))
        # Then PostgreSQL uniqueness leaves exactly one durable source job for the first range
        source_jobs = [item for item in self.jobs.list_jobs() if item["jobType"] == MaterialCourseService.JOB_TYPE]
        self.assertEqual(len(source_jobs), 1)

    def test_user_pauses_after_start_then_resumes_from_the_next_unfinished_batch(self) -> None:
        # Given a 31-page textbook and its first batch has been queued
        self._upload("whole-book-pause-resume", page_count=31, front_matter=False)
        started = self.whole_book.start("whole-book-pause-resume", self._request(), actor_id="teacher-a")
        self._worker().run_once()
        paused = self.whole_book.pause(started.runId, command_id="pause-1", actor_id="teacher-a")
        self.assertEqual(paused.status, "paused")
        jobs_after_pause = self.jobs.list_jobs()
        paused_retry = self.whole_book.pause(started.runId, command_id="pause-1", actor_id="teacher-a")
        self.assertEqual(paused_retry.status, "paused")
        self.assertEqual(self.jobs.list_jobs(), jobs_after_pause)
        with self.assertRaisesRegex(ValueError, "不能暂停"):
            self.whole_book.pause(started.runId, command_id="pause-2", actor_id="teacher-a")
        # When the current lesson finishes and the teacher continues the run
        self._drain(self._worker())
        between_batches = self.whole_book.get_run(started.runId)
        self.assertEqual(between_batches.status, "paused")
        self.assertEqual(between_batches.completedBatches, 1)
        self.assertEqual(between_batches.batches[1].status, "queued")
        resumed = self.whole_book.resume(started.runId, command_id="resume-1", actor_id="teacher-a")
        self.assertEqual(resumed.status, "running")
        jobs_after_resume = self.jobs.list_jobs()
        resumed_retry = self.whole_book.resume(started.runId, command_id="resume-1", actor_id="teacher-a")
        self.assertEqual(resumed_retry.status, "running")
        self.assertEqual(self.jobs.list_jobs(), jobs_after_resume)
        self._drain(self._worker())
        # Then the worker completes only the remaining original pages
        completed = self.whole_book.get_run(started.runId)
        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.completedPages, 31)
        self.assertEqual(completed.completedBatches, 2)

    def test_user_edits_a_generated_lesson_then_automation_preserves_it_and_stops(self) -> None:
        # Given a whole-book lesson whose automatic page generation just finished
        self._upload("whole-book-manual-edit", page_count=31, front_matter=False)
        started = self.whole_book.start("whole-book-manual-edit", self._request(), actor_id="teacher-a")
        worker = self._worker()
        worker.run_once()  # Start controller queues source preparation.
        worker.run_once()  # Source preparation queues its immutable plan.
        worker.run_once()  # Planning queues page generation.
        worker.run_once()  # Source completion notification observes queued child work.
        generated = worker.run_once()
        self.assertEqual(generated["jobType"], "courseware.document.generate")
        source_job = next(item for item in self.jobs.list_jobs() if item["jobType"] == MaterialCourseService.JOB_TYPE)
        chapter_id = source_job["result"]["chapters"][0]["chapterId"]
        document_id = self.courseware.list_chapter_documents(chapter_id)["items"][0]["coursewareId"]
        document = self.courseware.get_document(document_id)
        page = document["pages"][0]
        content = {key: page[key] for key in CoursewarePageContent.model_fields}
        content["teacherNotes"] = "Keep this teacher correction."
        self.courseware.edit_page(
            document_id,
            page["pageId"],
            content=CoursewarePageContent.model_validate(content),
            expected_record_version=document["recordVersion"],
        )
        # When the queued automatic check wakes up, it must respect that edit
        self._drain(worker)
        # Then the teacher's content remains and the run waits for human review
        saved = self.courseware.get_document(document_id)
        self.assertEqual(saved["pages"][0]["teacherNotes"], "Keep this teacher correction.")
        self.assertEqual(saved["automation"]["status"], "paused")
        self.assertEqual(self.whole_book.get_run(started.runId).status, "needs_attention")

    def test_user_selects_a_paper_then_the_original_exam_path_stays_unchanged(self) -> None:
        # Given an upload already classified as an exam
        self._upload("whole-book-paper", page_count=4, front_matter=False)
        upload = self.app_store.load_job("whole-book-paper")
        assert upload is not None
        upload["filename"] = "exam.pdf"
        upload["result"]["materialKind"] = "paper"
        self.app_store.save_job(upload)
        # When the teacher requests whole-book courseware
        with self.assertRaisesRegex(ValueError, "试卷"):
            self.whole_book.start("whole-book-paper", self._request(), actor_id="teacher-a")
        # Then no tutorial or courseware background job is created
        self.assertEqual(self.app_store.list_chapters(), [])
        self.assertEqual(self.jobs.list_jobs(), [])

    def test_user_selects_a_local_blank_page_then_the_partial_source_path_still_rejects_it(self) -> None:
        # Given an ordinary textbook selection that contains an OCR-empty page
        self._upload("whole-book-local-blank", page_count=4, front_matter=True)
        request = CoursewareSourcePreparationRequest(
            confirmedMaterialKind="textbook", subject="english", pageStart=2, pageEnd=2,
        )

        # When the teacher chooses that page only through the existing local-range flow
        with self.assertRaisesRegex(ValueError, "未识别"):
            self.material_courses.enqueue("whole-book-local-blank", request)

        # Then no source or whole-book task bypasses its existing OCR readiness gate
        self.assertEqual(self.jobs.list_jobs(), [])

    def test_user_sees_a_teaching_page_rejected_when_the_model_tries_to_skip_the_whole_batch(self) -> None:
        # Given instructional pages and a model that tries to classify every page as a cover
        self._upload("whole-book-malicious-skip", page_count=5, front_matter=False)
        self.courseware.runtime = WholeBookRuntime(skip_instructional_pages=True)
        started = self.whole_book.start("whole-book-malicious-skip", self._request(), actor_id="teacher-a")

        # When automatic planning returns the fabricated skip ledger
        self._drain(self._worker())

        # Then the original content check stops the run rather than claiming a completed book
        progress = self.whole_book.get_run(started.runId)
        self.assertEqual(progress.status, "needs_attention")
        self.assertEqual(progress.completedPages, 0)
        self.assertEqual(progress.batches[0].status, "failed")
        self.assertIn("不符合声明的跳过原因", progress.batches[0].message)

    def test_user_starts_through_the_teacher_route_then_get_progress_without_enqueuing_more_work(self) -> None:
        # Given a protected teacher session and one completed textbook upload
        self._upload("whole-book-http", page_count=35, front_matter=False)
        app = FastAPI()
        app.state.auth_mode = "protected"

        @app.middleware("http")
        async def trusted_actor(request: Request, call_next):
            role = request.headers.get("x-test-role")
            if role:
                request.state.actor = {"role": role, "sessionId": "teacher-session"}
            return await call_next(request)

        @app.exception_handler(AppError)
        async def app_error(request: Request, error: AppError) -> JSONResponse:
            return JSONResponse(status_code=error.status_code, content=error.to_problem("whole-book-test"))

        app.include_router(build_whole_book_router(self.whole_book))
        with TestClient(app) as client:
            forbidden = client.post(
                "/api/uploads/whole-book-http/courseware/whole-book",
                json={**self._request().model_dump(mode="json")},
                headers={"x-test-role": "student"},
            )
            self.assertEqual(forbidden.status_code, 403)
            started = client.post(
                "/api/uploads/whole-book-http/courseware/whole-book",
                json=self._request().model_dump(mode="json"),
                headers={"x-test-role": "teacher"},
            )
            self.assertEqual(started.status_code, 202, started.text)
            jobs_before_read = self.jobs.list_jobs()
            progress = client.get(
                f"/api/courseware/whole-book-runs/{started.json()['runId']}",
                headers={"x-test-role": "teacher"},
            )
        # Then progress survives a direct read without creating another worker task
        self.assertEqual(progress.status_code, 200, progress.text)
        self.assertEqual(progress.json()["status"], "queued")
        self.assertEqual(len(self.jobs.list_jobs()), len(jobs_before_read))

    def test_user_has_a_successful_first_batch_then_a_second_batch_conflict_preserves_it(self) -> None:
        # Given a textbook where the later chapter returns a deterministic planning conflict
        self._upload("whole-book-later-conflict", page_count=31, front_matter=False)
        self.courseware.runtime = WholeBookRuntime(fail_plan_at_page=31)
        started = self.whole_book.start("whole-book-later-conflict", self._request(), actor_id="teacher-a")
        # When the Worker produces the first lesson and reaches the failed later batch
        results = self._drain(self._worker())
        self.assertTrue(any(item["status"] == "failed" for item in results))
        # Then the first lesson remains published and only the second batch needs attention
        progress = self.whole_book.get_run(started.runId)
        self.assertEqual(progress.status, "needs_attention")
        self.assertEqual(progress.batches[0].status, "published")
        self.assertTrue(progress.batches[0].publicationIds)
        self.assertEqual(progress.batches[1].status, "failed")
