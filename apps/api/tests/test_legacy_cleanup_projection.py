"""User-visible filters and write fences for exact legacy cleanup manifests."""

from __future__ import annotations

import json
import os
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import insert, update

from application.services.chapter_courses import ChapterCourseService
from application.services.material_courses import MaterialCourseService
from domain.contracts.courseware import CoursewareSourcePreparationRequest
from infrastructure.files.upload_registry import UploadRegistry
from persistence.app_store import AppStore
from persistence.job_store import JobStore
from persistence.legacy_textbook_cleanup_store import legacy_textbook_cleanup_manifests
from persistence.schema import background_jobs, question_revisions, run_snapshots
from routers.learning_routes import build_learning_router
from routers.library_routes import build_library_router
from tests.postgres_test_support import PostgresTestCase


class LegacyCleanupProjectionAcceptanceTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        self.store = AppStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.store.close)
        self.jobs = JobStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.jobs.close)
        self.upload_id = "cleanup-source"
        directory = self.store.upload_root / self.upload_id
        directory.mkdir(parents=True)
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=300)
        writer.add_blank_page(width=200, height=300)
        writer.write(directory / "source.pdf")
        self.source_path = directory / "source.pdf"
        self.store.save_job({
            "uploadId": self.upload_id,
            "filename": "English textbook.pdf",
            "contentType": "application/pdf",
            "size": self.source_path.stat().st_size,
            "chunkSize": 1024,
            "totalChunks": 1,
            "directory": directory,
            "status": "complete",
            "sourceText": "<!-- page 1 -->\nA reading passage.\n<!-- page 2 -->\nA second passage.",
            "result": {
                "importId": "cleanup-import",
                "materialKind": "textbook",
                "extraction": {"pageCount": 2, "questionCount": 1},
                "questionPayload": {"question": {"id": "old-question", "prompt": "retired"}},
                "questionPayloads": [{"question": {"id": "old-question", "prompt": "retired"}}],
                "firstBatchJson": {
                    "batchId": "result-only-batch",
                    "questions": [{
                        "questionId": "old-question",
                        "lessonId": "old-lesson",
                        "prompt": "retired nested question",
                    }],
                },
            },
        })
        self.old_question = {"question": {"id": "old-question", "prompt": "retired"}, "lessonSteps": []}
        self.store.save_question(self.upload_id, "old-batch", self.old_question, [])
        self.store.save_lesson({
            "lessonId": "old-lesson",
            "sourceUploadId": self.upload_id,
            "title": "Retired lesson",
            "version": 1,
            "status": "in_review",
            "knowledgePoints": [],
            "blocks": [],
            "questionPayload": self.old_question,
            "guideCards": [],
        })
        self.old_job = self.jobs.create_job(
            "chapter.lesson.generate",
            {"uploadId": self.upload_id, "chapterId": "old-chapter", "lessonId": "old-lesson"},
            idempotency_key="old-generation-command",
        )
        with self.jobs.engine.begin() as connection:
            connection.execute(update(background_jobs).where(
                background_jobs.c.job_id == self.old_job["jobId"],
            ).values(status="failed", completed_at=time.time()))
        self.old_chapter_only_job = self.jobs.create_job(
            "chapter.lesson.generate", {"chapterId": "old-chapter", "lessonId": "old-lesson"},
            idempotency_key="old-generation-command-without-upload",
        )
        with self.jobs.engine.begin() as connection:
            connection.execute(update(background_jobs).where(
                background_jobs.c.job_id == self.old_chapter_only_job["jobId"],
            ).values(status="failed", completed_at=time.time()))
        revision = self.store.append_question_revision(
            upload_id=self.upload_id, source_question_key="old-batch", operation="edit",
            payload=self.old_question, guide_cards=[], run_id="old-run",
        )
        run = self.store.create_run_snapshot({
            "runId": "old-run", "operation": "generate", "scope": "upload",
            "targetUploadId": self.upload_id, "targetQuestionKey": "old-batch", "startedAt": time.time(),
        })
        self.registry_cache = UploadRegistry(
            store=self.store, lesson_store={}, default_guide_cards=[], pdf_tail_check_bytes=65536,
        )
        stale_job = self.store.load_job(self.upload_id)
        assert stale_job is not None
        self.registry_cache.uploads[self.upload_id] = stale_job
        self.registry_cache.lesson_store["old-question"] = {
            "payload": self.old_question, "sourceUploadId": self.upload_id,
        }
        marker = {
            "manifestId": "cleanup-manifest",
            "uploadId": self.upload_id,
            "chapterIds": ["old-chapter"],
            "lessonIds": ["old-lesson"],
            "batchIds": ["old-batch"],
            "questionIds": ["old-question"],
            "questionRevisionIds": [revision["revisionId"]],
            "jobIds": [self.old_job["jobId"], self.old_chapter_only_job["jobId"]],
            "runSnapshotIds": [run["runId"]],
            "lessonSearchDocumentIds": [],
            "sourceRevisionIds": [],
        }
        with self.store.engine.begin() as connection:
            connection.execute(insert(question_revisions).values(
                revision_id="old-question-revision",
                upload_id=self.upload_id,
                source_question_key="surviving-key",
                revision_number=1,
                operation="generate",
                previous_revision_id=None,
                payload_json=self.old_question,
                guide_cards_json=[],
                run_id="old-run-snapshot",
                created_at=time.time(),
                revision_source="model_generated",
            ))
            connection.execute(insert(run_snapshots).values(
                run_id="old-run-snapshot",
                operation="question.generate",
                scope="upload",
                target_upload_id=self.upload_id,
                target_question_key="old-batch",
                target_publication_id=None,
                status="succeeded",
                config_json={},
                result_json={"questionPayload": self.old_question},
                error_json=None,
                started_at=time.time(),
                completed_at=time.time(),
            ))
            connection.execute(legacy_textbook_cleanup_manifests.insert().values(
                manifest_id=marker["manifestId"], upload_id=self.upload_id,
                fingerprint="a" * 64,
                snapshot_json={
                    **marker,
                    "questionRevisionIds": ["old-question-revision"],
                    "runSnapshotIds": ["old-run", "old-run-snapshot"],
                },
                status="applied", applied_at=time.time(),
            ))

    def test_user_reopens_a_cleaned_textbook_then_old_answers_stay_hidden_while_source_preparation_remains_available(self):
        # Given a reversible cleanup marker for exact old chapters, lessons, questions, and jobs
        upload = self.store.load_job(self.upload_id)
        assert upload is not None
        # When the teacher opens the library after another process applied the cleanup
        self.assertEqual(upload["status"], "complete")
        self.assertIn("A reading passage", upload["sourceText"])
        self.assertEqual(upload["result"]["materialKind"], "textbook")
        self.assertIsNone(upload["result"]["questionPayload"])
        self.assertEqual(upload["result"]["questionPayloads"], [])
        self.assertNotIn("old-question", json.dumps(upload["result"], ensure_ascii=False))
        self.assertEqual(upload["batchPayloads"], {})
        self.assertIsNone(self.store.load_lesson("old-lesson"))
        self.assertNotIn("old-lesson", [item["lessonId"] for item in self.store.list_lessons()])
        self.assertEqual(self.store.list_question_revisions(self.upload_id, "old-batch"), [])
        self.assertEqual(self.store.list_question_revisions(self.upload_id, "surviving-key"), [])
        self.assertIsNone(self.store.get_run_snapshot("old-run-snapshot"))
        self.assertNotIn("old-run-snapshot", [
            item["runId"] for item in self.store.list_run_snapshots(upload_id=self.upload_id)
        ])
        self.assertIsNone(self.jobs.get_job(self.old_job["jobId"]))
        self.assertIsNone(self.jobs.get_job(self.old_chapter_only_job["jobId"]))
        self.assertIsNone(self.jobs.get_by_idempotency_key("old-generation-command-without-upload"))
        self.assertNotIn(self.old_job["jobId"], [item["jobId"] for item in self.jobs.list_jobs()])
        self.assertIsNone(self.jobs.retry_job(self.old_job["jobId"]))
        self.assertIsNone(self.jobs.retry_job(self.old_chapter_only_job["jobId"]))
        self.assertEqual(upload["result"]["firstBatchJson"]["questions"], [])
        self.assertEqual(self.store.list_question_revisions(self.upload_id, "old-batch"), [])
        self.assertIsNone(self.store.get_run_snapshot("old-run"))
        self.assertEqual(self.store.list_run_snapshots(upload_id=self.upload_id), [])
        with self.assertRaisesRegex(ValueError, "历史内容"):
            self.jobs.create_job(
                "chapter.lesson.generate", {"chapterId": "old-chapter"},
                idempotency_key="late-old-chapter-job",
            )

        app = FastAPI()
        app.include_router(build_learning_router(store=self.store))
        app.include_router(build_library_router(
            store=self.store, upload_registry=self.registry_cache,
            lesson_store=self.registry_cache.lesson_store, job_store=self.jobs,
        ))
        previous_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = self.database_url
        try:
            from routers import textbook_routes
        finally:
            if previous_database_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous_database_url
        imported_store = textbook_routes.store
        imported_job_store = textbook_routes.job_store
        self.addCleanup(imported_store.close)
        self.addCleanup(imported_job_store.close)
        textbook_routes.store = self.store
        textbook_routes.job_store = self.jobs
        app.include_router(textbook_routes.router)
        with TestClient(app) as client:
            lesson_link = client.get("/api/lessons/old-lesson")
            library = client.get("/api/library")
            library_item = client.get(f"/api/library/{self.upload_id}")
            retired_run = client.get("/api/runs/old-run")
            retired_revisions = client.get(
                f"/api/uploads/{self.upload_id}/questions/old-batch/revisions"
            )
        self.assertEqual(lesson_link.status_code, 404)
        self.assertEqual(library.status_code, 200, library.text)
        self.assertIn(self.upload_id, [item["uploadId"] for item in library.json()["items"]])
        self.assertEqual(library_item.status_code, 200, library_item.text)
        self.assertEqual(retired_run.status_code, 404)
        self.assertEqual(retired_revisions.status_code, 200)
        self.assertEqual(retired_revisions.json(), [])
        self.assertEqual(library_item.json()["questionPayloads"], [])
        self.assertIsNone(library_item.json()["questionPayload"])
        self.assertNotIn("old-question", json.dumps(library_item.json(), ensure_ascii=False))
        self.assertNotIn("old-question", self.registry_cache.lesson_store)

        # Old writer IDs are fenced by the same upload-row lock used by cleanup.
        with self.assertRaisesRegex(ValueError, "历史题目"):
            self.store.save_question(self.upload_id, "old-batch", self.old_question, [])
        with self.assertRaisesRegex(ValueError, "历史课程"):
            self.store.save_lesson({
                "lessonId": "old-lesson", "sourceUploadId": self.upload_id,
                "title": "late output", "version": 2, "status": "in_review",
                "knowledgePoints": [], "blocks": [], "questionPayload": {}, "guideCards": [],
            })

        # A later source-only preparation job targets no tombstoned derived ID.
        chapters = ChapterCourseService(self.store, self.jobs)
        prep = MaterialCourseService(chapters)
        request = CoursewareSourcePreparationRequest(
            subject="english", pageStart=1, pageEnd=2,
            sourceTitle="English textbook", license="CC BY 4.0",
            attributionText="Access for free at example.org.", distributionScope="classroom",
        )
        source_job = prep.enqueue(self.upload_id, request, idempotency_key="new-source-only-run")
        self.assertEqual(source_job["jobType"], "courseware.source.prepare")
        self.assertTrue(self.source_path.is_file())
