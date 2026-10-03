"""Independent API/Worker acceptance of source-bound AI drafts with real storage."""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from application.chapter_jobs import build_chapter_registry
from application.job_worker import JobWorker
from application.services.chapter_courses import ChapterCourseService
from persistence.app_store import AppStore
from persistence.job_store import JobStore
from routers.chapter_routes import build_chapter_router
from tests.postgres_test_support import PostgresTestCase


class SourceDraftRuntime:
    """External model boundary fixture; its output is not a semantic quality claim."""

    def __init__(self, during_call=None) -> None:
        self.during_call = during_call

    def generate_json(self, prompt: str, schema: dict[str, Any], **kwargs: Any):
        if self.during_call:
            self.during_call()
        source = json.loads(prompt.split("SOURCE_JSON=", 1)[1])
        page = source["pages"][0]
        first = page["sentences"][0]
        reference = {"sourceRevisionId": source["sourceRevisionId"], "page": page["page"],
                     "sentenceId": first["sentenceId"], "quote": first["text"]}
        if '"subject": "english"' in prompt:
            references = [{"sourceRevisionId": source["sourceRevisionId"], "page": item["page"],
                           "sentenceId": item["sentences"][0]["sentenceId"], "quote": item["sentences"][0]["text"]}
                          for item in source["pages"]]
            return {"questions": [{"kind": kind, "prompt": "Where did Mia move?", "answer": "London",
                                   "citations": references, "teacherVariants": ["to London"], "rubric": ["原文支持"]}
                                  for kind in ["word_meaning", "reference", "explicit", "inference"]]}, {"provider": "fixture"}
        return {
            "concept": {"text": "保持等式两边相等。", "citations": [reference]},
            "conditions": {"text": "两边进行相同的加减运算。", "citations": [reference]},
            "example": {"prompt": "解 x+2=5。", "answer": "3", "steps": ["两边减去 2", "得到 x=3"], "citations": [reference]},
            "hints": [{"text": text, "citations": [reference]} for text in ["观察常数项", "等式两边减去相同数", "计算 5-2"]],
            "check": {"prompt": "解 x+2=5，x 等于多少？", "answer": "3", "citations": [reference]},
        }, {"provider": "fixture", "model": "source-draft-fixture"}


class ChapterQualityPostgresTests(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = AppStore(database_url=self.database_url, data_root=self.data_root)
        self.jobs = JobStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.store.close)
        self.addCleanup(self.jobs.close)
        self.runtime = SourceDraftRuntime()
        self.service = ChapterCourseService(self.store, jobs=self.jobs, generation_runtime=self.runtime)
        app = FastAPI()
        app.include_router(build_chapter_router(self.service))
        self.client = self.enterContext(TestClient(app))
        self.worker = JobWorker(self.jobs, build_chapter_registry(self.service), worker_id="chapter-quality-acceptance")
        self.chapter = self.service.create({"subject": "math", "title": "等式", "source": {
            "license": "Original test fixture", "pages": [{"page": 1,
                "text": "等式两边进行相同加减运算仍然相等。解 x+2=5，得到 x=3。"}],
        }})

    def _queue(self):
        response = self.client.post(f"/api/chapters/{self.chapter['chapterId']}/generate-ai", json={
            "expectedRecordVersion": self.chapter["recordVersion"],
        })
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def test_user_generates_and_refreshes_then_one_unapproved_draft_can_be_reviewed_and_published_safely(self) -> None:
        # Given a chapter with source evidence and a queued model generation
        queued = self._queue()
        self.assertEqual(self._queue()["jobId"], queued["jobId"])
        # When a real Worker finishes and the author reloads the HTTP DTO
        finished = self.worker.run_once()
        self.assertEqual(finished["status"], "succeeded", finished.get("lastError"))
        chapter_id = self.chapter["chapterId"]
        managed = self.client.get(f"/api/chapters/{chapter_id}")
        self.assertEqual(managed.status_code, 200, managed.text)
        chapter = managed.json()
        self.assertEqual(chapter["generationJobId"], queued["jobId"])
        self.assertEqual(len(chapter["currentLessonIds"]), 1)
        lesson = chapter["lessons"][0]
        self.assertEqual(lesson["status"], "in_review")
        self.assertEqual([block["payload"]["hint"] for block in lesson["blocks"] if block["type"] == "hint"],
                         ["观察常数项", "等式两边减去相同数", "计算 5-2"])
        self.assertEqual(self.client.post(f"/api/chapters/{chapter_id}/publish").status_code, 409)
        # Then only explicit teacher approval permits a safe published projection
        reviewed = self.client.patch(f"/api/chapters/{chapter_id}/lessons/{lesson['lessonId']}/review", json={
            "expectedRecordVersion": chapter["recordVersion"], "decision": "approve", "reviewer": "acceptance-teacher",
        })
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        published = self.client.post(f"/api/chapters/{chapter_id}/publish")
        self.assertEqual(published.status_code, 201, published.text)
        public = self.client.get(f"/api/chapters/{chapter_id}/published").json()
        question = public["lessons"][0]["questionPayload"]["question"]
        self.assertNotIn("correctAnswers", question)
        self.assertNotIn("requiredEvidenceRefs", question)
        quiz = next(block for block in public["lessons"][0]["blocks"] if block["type"] == "quiz")
        self.assertNotIn("answerDraft", quiz["payload"])
        self.assertNotIn("sourceRefs", quiz["payload"])

    def test_user_revises_source_during_model_call_then_worker_keeps_the_new_revision_and_rejects_old_output(self) -> None:
        # Given a model call racing with a source revision
        chapter_id = self.chapter["chapterId"]
        self.runtime.during_call = lambda: self.service.revise(chapter_id, {
            "expectedRecordVersion": self.chapter["recordVersion"], "source": {
                "license": "Original fixture", "sourceVersion": "2", "pages": [{"page": 1, "text": "新的来源 x+3=6。"}],
            },
        })
        self._queue()
        # When the Worker attempts to write the older generated result
        result = self.worker.run_once()
        # Then no draft replaces the new source and the stale job is terminal
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["lastError"]["retryable"])
        self.assertEqual(self.service.get(chapter_id)["currentLessonIds"], [])
        self.assertEqual(self.service.get(chapter_id)["version"], 2)

    def test_user_cancels_while_generation_is_finishing_then_no_generated_course_survives(self) -> None:
        # Given a queued draft and cancellation requested during its external model call
        queued = self._queue()
        self.runtime.during_call = lambda: self.jobs.request_cancel(queued["jobId"])
        # When the Worker returns from the model
        result = self.worker.run_once()
        # Then cancellation is durable and no lesson can be published
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.service.get(self.chapter["chapterId"])["currentLessonIds"], [])

    def test_user_retries_after_transient_model_failure_then_the_same_job_produces_one_draft(self) -> None:
        # Given a temporary error at the real external model boundary
        def unavailable():
            raise OSError("external model unavailable")
        self.runtime.during_call = unavailable
        queued = self._queue()
        failed = self.worker.run_once()
        self.assertEqual(failed["status"], "failed")
        self.assertTrue(failed["lastError"]["retryable"])
        # When the provider recovers and the user retries
        self.runtime.during_call = None
        retried = self.jobs.retry_job(queued["jobId"])
        self.assertEqual(retried["status"], "queued")
        result = self.worker.run_once()
        # Then there is one linked draft, with the same durable job identity
        self.assertEqual(result["status"], "succeeded", result.get("lastError"))
        self.assertEqual(result["jobId"], queued["jobId"])
        self.assertEqual(len(self.service.get(self.chapter["chapterId"])["currentLessonIds"]), 1)

    def test_user_corrects_a_generated_math_question_then_review_and_publication_keep_the_corrected_content(self) -> None:
        # Given an unapproved generated math lesson
        self._queue()
        self.worker.run_once()
        chapter_id = self.chapter["chapterId"]
        chapter = self.service.get(chapter_id)
        lesson = chapter["lessons"][0]
        # When the teacher corrects its question, numeric answer and hints
        edited = self.client.put(f"/api/chapters/{chapter_id}/lessons/{lesson['lessonId']}", json={
            "expectedRecordVersion": chapter["recordVersion"],
            "sourceRevisionId": lesson["sourceRevisionId"], "page": 1,
            "prompt": "5 减去 2 等于多少？", "answer": "3", "answerType": "numeric",
            "hints": ["先读题", "计算减法", "检查结果"],
        })
        self.assertEqual(edited.status_code, 200, edited.text)
        # Then explicit review accepts the edited answer and student content agrees
        reviewed = self.client.patch(f"/api/chapters/{chapter_id}/lessons/{lesson['lessonId']}/review", json={
            "expectedRecordVersion": edited.json()["recordVersion"], "decision": "approve", "reviewer": "teacher",
        })
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        self.assertEqual(self.client.post(f"/api/chapters/{chapter_id}/publish").status_code, 201)
        public = self.client.get(f"/api/chapters/{chapter_id}/published").json()["lessons"][0]
        quiz = next(block for block in public["blocks"] if block["type"] == "quiz")
        self.assertEqual(quiz["payload"]["prompt"], "5 减去 2 等于多少？")
        self.assertEqual([block["payload"]["hint"] for block in public["blocks"] if block["type"] == "hint"],
                         ["先读题", "计算减法", "检查结果"])

    def test_user_edits_an_english_question_then_its_distinct_source_pages_are_preserved(self) -> None:
        # Given a generated English question citing two distinct original pages
        self.chapter = self.service.create({"subject": "english", "title": "Reading", "source": {
            "license": "Original fixture", "pages": [{"page": 1, "text": "Mia moved to London."},
                                                     {"page": 2, "text": "She likes its parks."}],
        }})
        self._queue()
        self.worker.run_once()
        chapter = self.service.get(self.chapter["chapterId"])
        lesson = chapter["lessons"][0]
        # When a teacher changes the question and submits a legacy concept field
        edited = self.client.put(f"/api/chapters/{chapter['chapterId']}/lessons/{lesson['lessonId']}", json={
            "expectedRecordVersion": chapter["recordVersion"], "sourceRevisionId": lesson["sourceRevisionId"],
            "page": 1, "prompt": "Which city did Mia move to?", "answer": "London",
            "conceptMarkdown": "Mia moved to London.",
        })
        self.assertEqual(edited.status_code, 200, edited.text)
        # Then the reading passage remains tied to the unmodified source snapshots
        saved = next(item for item in edited.json()["lessons"] if item["lessonId"] == lesson["lessonId"])
        self.assertEqual([block["payload"]["markdown"] for block in saved["blocks"] if block["type"] == "markdown"],
                         ["Mia moved to London.", "She likes its parks."])
