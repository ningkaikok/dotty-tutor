"""PostgreSQL acceptance checks for chapter revisions, reviews, and attempts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from application.services.chapter_courses import ChapterCourseService
from persistence.app_store import AppStore
from routers.chapter_routes import build_chapter_router
from routers.learning_routes import build_learning_router
from tests.postgres_test_support import PostgresTestCase


class ChapterPostgresAcceptanceTests(PostgresTestCase):
    """Exercise the public chapter routes with the real migrated PostgreSQL store."""

    def setUp(self) -> None:
        super().setUp()
        self.store = AppStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.store.engine.dispose)
        self.service = ChapterCourseService(self.store)
        self.client = self._client(self.store)
        self.addCleanup(self.client.close)

    @staticmethod
    def _client(store: AppStore) -> TestClient:
        app = FastAPI()
        app.include_router(build_chapter_router(ChapterCourseService(store)))
        app.include_router(build_learning_router(store=store))
        return TestClient(app, raise_server_exceptions=False)

    def _reloaded_client(self) -> tuple[AppStore, TestClient]:
        store = AppStore(database_url=self.database_url, data_root=self.data_root)
        client = self._client(store)
        self.addCleanup(client.close)
        self.addCleanup(store.engine.dispose)
        return store, client

    def _temporary_client(self) -> tuple[AppStore, TestClient]:
        store = AppStore(database_url=self.database_url, data_root=self.data_root)
        client = self._client(store)
        return store, client

    @staticmethod
    def _close_temporary_client(store: AppStore, client: TestClient) -> None:
        client.close()
        store.engine.dispose()

    @staticmethod
    def _student_evidence(
        lesson: dict[str, Any], required_refs: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        question = lesson["questionPayload"]["question"]
        required = required_refs or question.get("requiredEvidenceRefs", [])
        options = lesson["evidenceOptions"]
        if not required:
            # Student projections intentionally omit teacher-authored target refs.
            # A one-sentence math source has one unambiguous selectable citation.
            return options[:1]
        selected = []
        for reference in required:
            candidate = next((item for item in options if all(
                item.get(key) == reference.get(key)
                for key in ("sourceRevisionId", "page", "sentenceId", "regionId")
                if reference.get(key) is not None
            )), None)
            if candidate is None:
                raise AssertionError(f"No published evidence option for authored reference {reference!r}")
            selected.append(candidate)
        return selected

    def _create_chapter(self, subject: str, title: str, pages: list[dict[str, Any]], **source_fields: Any) -> dict[str, Any]:
        response = self.client.post("/api/chapters", json={
            "subject": subject,
            "title": title,
            "source": {
                "sourceVersion": "synthetic-source-v1",
                "license": "Original synthetic test content",
                "pages": pages,
                **source_fields,
            },
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _approve_lesson(self, chapter_id: str, lesson_id: str, reviewer: str) -> Any:
        return self.client.patch(f"/api/chapters/{chapter_id}/lessons/{lesson_id}/review", json={
            "decision": "approve", "reviewer": reviewer,
            "expectedRecordVersion": self._record_version(chapter_id),
        })

    def _record_version(self, chapter_id: str) -> int:
        chapter = self.client.get(f"/api/chapters/{chapter_id}")
        self.assertEqual(chapter.status_code, 200, chapter.text)
        return chapter.json()["recordVersion"]

    def _generate_edit_approve_publish_math(self, chapter: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        chapter_id = chapter["chapterId"]
        generated_response = self.client.post(f"/api/chapters/{chapter_id}/generate")
        self.assertEqual(generated_response.status_code, 200, generated_response.text)
        generated = generated_response.json()
        lesson = generated["lessons"][0]
        revision_id = chapter["sourceRevisions"][-1]["sourceRevisionId"]
        edited = self.client.put(f"/api/chapters/{chapter_id}/lessons/{lesson['lessonId']}", json={
            "prompt": "当 x=1 时，求 y=2x+1 的值。",
            "answer": "3",
            "answerType": "numeric",
            "sourceRevisionId": revision_id,
            "page": 1,
            "expectedRecordVersion": self._record_version(chapter_id),
        })
        self.assertEqual(edited.status_code, 200, edited.text)
        reviewed = self._approve_lesson(chapter_id, lesson["lessonId"], "teacher-math-test")
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        published = self.client.post(f"/api/chapters/{chapter_id}/publish")
        self.assertEqual(published.status_code, 201, published.text)
        public_response = self.client.get(f"/api/chapters/{chapter_id}/published")
        self.assertEqual(public_response.status_code, 200, public_response.text)
        public_lesson = public_response.json()["lessons"][0]
        return published.json(), public_lesson

    def test_user_publishes_and_retries_a_math_attempt_then_database_reload_keeps_one_learning_record(self) -> None:
        # Given a no-upload math source, an authored numeric question, and a teacher-approved publication
        chapter = self._create_chapter("math", "一次函数", [{
            "page": 1,
            "text": "一次函数 y=2x+1，当 x=1 时，求 y 的值。",
            "regions": [{"regionId": "equation", "x": 0.1, "y": 0.2, "width": 0.8, "height": 0.3}],
        }])
        publication, lesson = self._generate_edit_approve_publish_math(chapter)
        request = {
            "attemptId": "chapter-math-attempt-1",
            "publicationId": publication["publicationId"],
            "lessonId": lesson["lessonId"],
            "questionId": lesson["lessonId"],
            "learnerId": "chapter-math-learner",
            "answer": {"numericAnswer": "3"},
            "evidenceRefs": self._student_evidence(lesson),
        }

        # When a learner submits the right answer, reloads the store, and retries the same attempt
        first = self.client.post(f"/api/chapters/{chapter['chapterId']}/attempts", json=request)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["assessment"], "correct", first.json())
        before_retry = self.store.list_mastery("chapter-math-learner")
        reloaded_store, reloaded_client = self._reloaded_client()
        retried = reloaded_client.post(f"/api/chapters/{chapter['chapterId']}/attempts", json=request)
        self.assertEqual(retried.status_code, 200, retried.text)
        after_retry = reloaded_store.list_mastery("chapter-math-learner")

        # Then the source-bound result survives and retry does not add mastery evidence
        self.assertEqual(retried.json()["assessment"], "correct", retried.json())
        self.assertEqual(len(after_retry), 1)
        self.assertEqual(after_retry[0]["attemptCount"], before_retry[0]["attemptCount"])
        self.assertEqual(after_retry[0]["evidenceCount"], before_retry[0]["evidenceCount"])

        forged_quote_request = {
            **request,
            "attemptId": "chapter-math-forged-quote",
            "evidenceRefs": [{**request["evidenceRefs"][0], "quote": "unrelated forged sentence"}],
        }
        forged_quote = reloaded_client.post(
            f"/api/chapters/{chapter['chapterId']}/attempts", json=forged_quote_request,
        )
        self.assertEqual(forged_quote.status_code, 200, forged_quote.text)
        self.assertEqual(forged_quote.json()["evidenceVerdict"], "mismatch")
        self.assertEqual(reloaded_store.list_mastery("chapter-math-learner"), after_retry)

        # An attempt with no source evidence remains visible for review without entering mastery.
        unresolved_request = {**request, "attemptId": "chapter-math-needs-review", "evidenceRefs": []}
        unresolved = reloaded_client.post(f"/api/chapters/{chapter['chapterId']}/attempts", json=unresolved_request)
        self.assertEqual(unresolved.status_code, 200, unresolved.text)
        self.assertEqual(unresolved.json()["assessment"], "needs_review")
        after_unresolved = reloaded_store.list_mastery("chapter-math-learner")
        self.assertEqual(after_unresolved, after_retry)
        reloaded_again, reloaded_again_client = self._reloaded_client()
        restored_unresolved = reloaded_again_client.get(
            f"/api/chapters/{chapter['chapterId']}/attempts/{unresolved_request['attemptId']}",
            params={"learnerId": "chapter-math-learner"},
        )
        self.assertEqual(restored_unresolved.status_code, 200, restored_unresolved.text)
        self.assertEqual(restored_unresolved.json()["assessment"], "needs_review")
        self.assertEqual(reloaded_again.list_mastery("chapter-math-learner"), after_retry)

        wrong_learner = reloaded_client.get(
            f"/api/chapters/{chapter['chapterId']}/attempts/{request['attemptId']}",
            params={"learnerId": "different-learner"},
        )
        self.assertEqual(wrong_learner.status_code, 404)
        collided_request = {**request, "answer": {"numericAnswer": "4"}}
        collision = reloaded_client.post(f"/api/chapters/{chapter['chapterId']}/attempts", json=collided_request)
        self.assertEqual(collision.status_code, 409)

    def test_user_revises_a_published_source_then_the_old_publication_and_attempt_remain_readable(self) -> None:
        # Given a published math source and an attempt tied to its immutable publication
        chapter = self._create_chapter("math", "一次函数修订", [{
            "page": 1, "text": "一次函数 y=2x+1，当 x=1 时，求 y 的值。",
        }])
        old_publication, old_lesson = self._generate_edit_approve_publish_math(chapter)
        old_attempt = self.client.post(f"/api/chapters/{chapter['chapterId']}/attempts", json={
            "attemptId": "chapter-math-old-attempt",
            "publicationId": old_publication["publicationId"],
            "lessonId": old_lesson["lessonId"],
            "questionId": old_lesson["lessonId"],
            "learnerId": "chapter-revision-learner",
            "answer": {"numericAnswer": "3"},
            "evidenceRefs": self._student_evidence(old_lesson),
        })
        self.assertEqual(old_attempt.status_code, 200, old_attempt.text)
        self.assertEqual(old_attempt.json()["assessment"], "correct")
        old_revision_id = old_lesson["sourceRevisionId"]

        # When the teacher creates a new source revision with a changed and added page
        revised = self.client.post(f"/api/chapters/{chapter['chapterId']}/revisions", json={
            "expectedRecordVersion": self._record_version(chapter["chapterId"]),
            "source": {
                "sourceVersion": "synthetic-source-v2",
                "license": "Original synthetic test content",
                "pageStart": 1,
                "pageEnd": 2,
                "pages": [
                    {"page": 1, "text": "一次函数 y=2x+1；修订说明：本页已更新。"},
                    {"page": 2, "text": "新增页：一次函数 y=3x-1。"},
                ],
            },
        })
        self.assertEqual(revised.status_code, 200, revised.text)
        new_revision_id = revised.json()["sourceRevisions"][-1]["sourceRevisionId"]
        self.assertNotEqual(new_revision_id, old_revision_id)

        # Publish a changed revision, then submit another attempt against the still-open old publication.
        generated = self.client.post(f"/api/chapters/{chapter['chapterId']}/generate")
        self.assertEqual(generated.status_code, 200, generated.text)
        new_lessons = [item for item in generated.json()["lessons"] if item["lessonId"] in generated.json()["currentLessonIds"]]
        new_lesson = next(item for item in new_lessons if item["sourceLocator"]["page"] == 2)
        edit = self.client.put(f"/api/chapters/{chapter['chapterId']}/lessons/{new_lesson['lessonId']}", json={
            "prompt": "当 x=1 时求 y=3x-1 的值。", "answer": "2", "answerType": "numeric",
            "sourceRevisionId": new_revision_id, "page": 2,
            "expectedRecordVersion": self._record_version(chapter["chapterId"]),
        })
        self.assertEqual(edit.status_code, 200, edit.text)
        for lesson in new_lessons:
            review = self._approve_lesson(chapter["chapterId"], lesson["lessonId"], "teacher-revision-test")
            self.assertEqual(review.status_code, 200, review.text)
        new_publication = self.client.post(f"/api/chapters/{chapter['chapterId']}/publish")
        self.assertEqual(new_publication.status_code, 201, new_publication.text)
        self.assertNotEqual(new_publication.json()["publicationId"], old_publication["publicationId"])
        old_session_attempt = self.client.post(f"/api/chapters/{chapter['chapterId']}/attempts", json={
            "attemptId": "chapter-math-old-session-after-new-pub",
            "publicationId": old_publication["publicationId"],
            "lessonId": old_lesson["lessonId"], "questionId": old_lesson["lessonId"],
            "learnerId": "chapter-revision-learner", "answer": {"numericAnswer": "3"},
            "evidenceRefs": self._student_evidence(old_lesson),
        })
        self.assertEqual(old_session_attempt.status_code, 200, old_session_attempt.text)
        self.assertEqual(old_session_attempt.json()["assessment"], "correct")

        # Then the old source snapshot and attempt still resolve through their old publication
        old_student_view = self.client.get(
            f"/api/chapters/{chapter['chapterId']}/published",
            params={"publicationId": old_publication["publicationId"]},
        )
        self.assertEqual(old_student_view.status_code, 200, old_student_view.text)
        self.assertEqual(old_student_view.json()["lessons"][0]["sourceRevisionId"], old_revision_id)
        self.assertIn("一次函数 y=2x+1", str(old_student_view.json()["lessons"][0]["blocks"]))
        self.assertNotIn("修订说明", str(old_student_view.json()["lessons"][0]["blocks"]))
        restored_attempt = self.client.get(
            f"/api/chapters/{chapter['chapterId']}/attempts/{old_attempt.json()['attemptId']}",
            params={"learnerId": "chapter-revision-learner"},
        )
        self.assertEqual(restored_attempt.status_code, 200, restored_attempt.text)
        self.assertEqual(restored_attempt.json()["publicationId"], old_publication["publicationId"])
        self.assertEqual(restored_attempt.json()["assessment"], "correct")

    def test_user_edits_an_approved_question_then_publication_waits_for_new_review(self) -> None:
        # Given a reviewed math question that was approved once
        chapter = self._create_chapter("math", "审核后修改", [{
            "page": 1, "text": "一次函数 y=2x+1，当 x=1 时，求 y 的值。",
        }])
        chapter_id = chapter["chapterId"]
        generated = self.client.post(f"/api/chapters/{chapter_id}/generate").json()
        lesson = generated["lessons"][0]
        revision_id = chapter["sourceRevisions"][-1]["sourceRevisionId"]
        body = {"prompt": "当 x=1 时求 y。", "answer": "3", "answerType": "numeric", "sourceRevisionId": revision_id, "page": 1,
                "expectedRecordVersion": self._record_version(chapter_id)}
        self.assertEqual(self.client.put(f"/api/chapters/{chapter_id}/lessons/{lesson['lessonId']}", json=body).status_code, 200)
        self.assertEqual(self._approve_lesson(chapter_id, lesson["lessonId"], "teacher").status_code, 200)

        # When the approved answer is edited
        body["answer"] = "4"
        body["expectedRecordVersion"] = self._record_version(chapter_id)
        edited = self.client.put(f"/api/chapters/{chapter_id}/lessons/{lesson['lessonId']}", json=body)
        self.assertEqual(edited.status_code, 200, edited.text)
        blocked = self.client.post(f"/api/chapters/{chapter_id}/publish")

        # Then the old approval is cleared and a new explicit review is required
        self.assertEqual(blocked.status_code, 409)
        approved_again = self._approve_lesson(chapter_id, lesson["lessonId"], "teacher")
        self.assertEqual(approved_again.status_code, 200, approved_again.text)
        published = self.client.post(f"/api/chapters/{chapter_id}/publish")
        self.assertEqual(published.status_code, 201, published.text)

    def test_user_has_a_missing_page_or_flagged_source_then_publication_is_blocked(self) -> None:
        # Given source sections with a page gap or an unresolved source quality flag
        cases = [
            ({"pageStart": 1, "pageEnd": 2}, [{"page": 1, "text": "一次函数 y=2x+1。"}]),
            ({}, [{"page": 1, "text": "一次函数 y=2x+1。", "flags": ["wrong_figure"]}]),
        ]
        for index, (source_fields, pages) in enumerate(cases):
            with self.subTest(case=index):
                chapter = self._create_chapter("math", f"来源门禁-{index}", pages, **source_fields)
                chapter_id = chapter["chapterId"]
                generated = self.client.post(f"/api/chapters/{chapter_id}/generate")
                self.assertEqual(generated.status_code, 200, generated.text)
                for lesson in generated.json()["lessons"]:
                    reviewed = self._approve_lesson(chapter_id, lesson["lessonId"], "teacher-source-test")
                    self.assertEqual(reviewed.status_code, 200, reviewed.text)

                # When every generated lesson has a teacher review
                publish = self.client.post(f"/api/chapters/{chapter_id}/publish")

                # Then an incomplete or flagged source still cannot be published
                self.assertEqual(publish.status_code, 409)

    def test_user_selects_a_page_from_a_persisted_synthetic_ocr_snapshot_then_invalid_provenance_or_bounds_fail_closed(self) -> None:
        # Given an explicitly synthetic, already-produced OCR artifact persisted in the real store
        upload_id = "synthetic-ocr-snapshot-middle-page"
        source_text = (
            "<!-- page 1 -->第一页：y=1。\n"
            "<!-- page 2 -->第二页：一次函数 y=2x+1。\n"
            "<!-- page 3 -->第三页：y=3x-1。"
        )
        self.store.save_job({
            "uploadId": upload_id, "filename": "synthetic-ocr.txt", "contentType": "text/plain",
            "size": len(source_text.encode("utf-8")), "chunkSize": 1, "totalChunks": 1,
            "sourceText": source_text, "directory": str(self.data_root), "status": "complete",
            "progress": 100, "message": "Synthetic OCR snapshot ready", "completedAt": 1.0,
        })
        self.assertEqual(self.store.load_job(upload_id)["sourceText"], source_text)

        # When the teacher selects only the middle page and names the OCR source version
        created = self.client.post("/api/chapters", json={
            "subject": "math", "title": "合成OCR中页",
            "source": {
                "uploadId": upload_id, "sourceVersion": "synthetic-ocr-v1",
                "license": "Original synthetic test content", "pageStart": 2, "pageEnd": 2,
            },
        })

        # Then the chapter retains exactly the selected page and its source provenance
        self.assertEqual(created.status_code, 201, created.text)
        revision = created.json()["sourceRevisions"][0]
        self.assertEqual(revision["uploadId"], upload_id)
        self.assertEqual(revision["sourceVersion"], "synthetic-ocr-v1")
        self.assertEqual([page["page"] for page in revision["pages"]], [2])
        self.assertEqual(revision["pages"][0]["text"], "第二页：一次函数 y=2x+1。")

        # Missing, unfinished, or mismatched upload snapshots are rejected.
        missing_upload = self.client.post("/api/chapters", json={
            "subject": "math", "title": "缺失OCR任务",
            "source": {"uploadId": "missing-synthetic-task", "license": "synthetic", "pages": []},
        })
        self.assertEqual(missing_upload.status_code, 404, missing_upload.text)
        self.store.save_job({
            "uploadId": "synthetic-ocr-not-ready", "filename": "pending.txt", "contentType": "text/plain",
            "size": 1, "chunkSize": 1, "totalChunks": 1, "sourceText": "<!-- page 1 -->待完成",
            "directory": str(self.data_root), "status": "running", "progress": 50,
        })
        unfinished = self.client.post("/api/chapters", json={
            "subject": "math", "title": "未完成OCR任务",
            "source": {"uploadId": "synthetic-ocr-not-ready", "license": "synthetic", "pages": []},
        })
        self.assertEqual(unfinished.status_code, 409, unfinished.text)
        forged_text = self.client.post("/api/chapters", json={
            "subject": "math", "title": "伪造OCR快照",
            "source": {
                "uploadId": upload_id, "license": "synthetic", "pageStart": 2, "pageEnd": 2,
                "pages": [{"page": 2, "text": "伪造内容 y=999。"}],
            },
        })
        self.assertEqual(forged_text.status_code, 409, forged_text.text)

        # Oversized ranges, regions outside the page, and more than 200 sentence entries fail as client errors.
        invalid_sources = [
            {"license": "synthetic", "pageStart": 1, "pageEnd": 1_000_000_000,
             "pages": [{"page": 1, "text": "一次函数 y=2x+1。"}]},
            {"license": "synthetic", "pages": [{"page": 1, "text": "一次函数 y=2x+1。",
             "regions": [{"regionId": "outside", "x": 0.8, "y": 0.1, "width": 0.3, "height": 0.2}]}]},
            {"license": "synthetic", "pages": [{"page": 1, "text": "x." * 201}]},
        ]
        for index, source in enumerate(invalid_sources):
            with self.subTest(invalid_source=index):
                chapters_before = len(self.store.list_chapters())
                rejected = self.client.post("/api/chapters", json={
                    "subject": "math", "title": f"越界输入-{index}", "source": source,
                })
                self.assertIn(rejected.status_code, {400, 409, 422}, rejected.text)
                self.assertEqual(len(self.store.list_chapters()), chapters_before)

    def test_user_answers_four_english_kinds_then_a_paraphrase_is_reviewed_without_changing_math_mastery(self) -> None:
        # Given existing math mastery and a source-backed English reading chapter
        math_chapter = self._create_chapter("math", "English regression guard", [{
            "page": 1, "text": "一次函数 y=2x+1，当 x=1 时，求 y 的值。",
        }])
        math_publication, math_lesson = self._generate_edit_approve_publish_math(math_chapter)
        math_attempt = self.client.post(f"/api/chapters/{math_chapter['chapterId']}/attempts", json={
            "attemptId": "chapter-shared-math-attempt",
            "publicationId": math_publication["publicationId"],
            "lessonId": math_lesson["lessonId"], "questionId": math_lesson["lessonId"],
            "learnerId": "chapter-shared-learner", "answer": {"numericAnswer": "3"},
            "evidenceRefs": self._student_evidence(math_lesson),
        })
        self.assertEqual(math_attempt.status_code, 200, math_attempt.text)
        self.assertEqual(math_attempt.json()["assessment"], "correct")
        math_mastery_before = self.store.list_mastery("chapter-shared-learner")
        english_pages = [
            {"page": 1, "text": "Mia is a bright student who solves puzzles quickly."},
            {"page": 2, "text": "The volunteers arrived early. They set up the tables."},
            {"page": 3, "text": "The notice says the train leaves at nine o'clock."},
            {"page": 4, "text": "The road was wet and drops still fell from the trees."},
        ]
        chapter = self._create_chapter("english", "Synthetic reading checks", english_pages)
        chapter_id = chapter["chapterId"]
        generated = self.client.post(f"/api/chapters/{chapter_id}/generate")
        self.assertEqual(generated.status_code, 200, generated.text)
        lessons = generated.json()["lessons"]
        questions = [lesson["questionPayload"]["question"] for lesson in lessons]
        revision_id = chapter["sourceRevisions"][-1]["sourceRevisionId"]
        edits = [
            ("word_meaning", "objective", "What does bright mean?", "intelligent", ["intelligent"], {"supportStatus": "supported"}),
            ("reference", "objective", "Who does they refer to?", "the volunteers", ["the volunteers"], {"supportStatus": "supported"}),
            ("explicit", "objective", "When does the train leave?", "at nine o'clock", ["at nine o'clock"], {"supportStatus": "supported"}),
            ("inference", "short_answer", "What probably happened recently?", "It probably rained recently.", ["It probably rained recently."], {"supportStatus": "supported"}),
        ]
        edited_lessons: list[dict[str, Any]] = []
        required_refs_by_lesson: dict[str, list[dict[str, Any]]] = {}
        for lesson, question, (kind, mode, prompt, answer, accepted, rubric) in zip(lessons, questions, edits, strict=True):
            request = {
                "prompt": prompt, "answer": answer, "answerType": "text",
                "questionKind": kind, "answerMode": mode, "acceptedAnswers": accepted,
                "requiredEvidenceRefs": question["requiredEvidenceRefs"], "rubric": rubric,
                "sourceRevisionId": revision_id,
                "page": (question.get("sourceLocator") or {}).get("page")
                or (lesson.get("sourceLocator") or {}).get("page") or 1,
                "expectedRecordVersion": self._record_version(chapter_id),
            }
            response = self.client.put(f"/api/chapters/{chapter_id}/lessons/{lesson['lessonId']}", json=request)
            self.assertEqual(response.status_code, 200, response.text)
            edited_lessons.append(lesson)
            required_refs_by_lesson[lesson["lessonId"]] = request["requiredEvidenceRefs"]
        for lesson in edited_lessons:
            reviewed = self._approve_lesson(chapter_id, lesson["lessonId"], "teacher-english-test")
            self.assertEqual(reviewed.status_code, 200, reviewed.text)
        publication = self.client.post(f"/api/chapters/{chapter_id}/publish")
        self.assertEqual(publication.status_code, 201, publication.text)
        public = self.client.get(f"/api/chapters/{chapter_id}/published").json()
        legacy_session = self.client.post("/api/learning/sessions", json={
            "learnerId": "chapter-shared-learner", "publicationId": public["publicationId"],
        })
        self.assertIn(legacy_session.status_code, {400, 404, 409}, legacy_session.text)

        # When approved answers are submitted and an unlisted paraphrase is referred for review
        attempts = []
        answers = [
            {"text": "intelligent"},
            {"text": "the volunteers"},
            {"text": "at nine o'clock"},
            {"text": "There was recent rain."},
        ]
        for index, (lesson, answer) in enumerate(zip(public["lessons"], answers, strict=True)):
            response = self.client.post(f"/api/chapters/{chapter_id}/attempts", json={
                "attemptId": f"chapter-english-attempt-{index}",
                "publicationId": public["publicationId"],
                "lessonId": lesson["lessonId"], "questionId": lesson["lessonId"],
                "learnerId": "chapter-shared-learner", "answer": answer,
                "evidenceRefs": self._student_evidence(lesson, required_refs_by_lesson[lesson["lessonId"]]),
            })
            self.assertEqual(response.status_code, 200, response.text)
            attempts.append(response.json())
        self.assertEqual([item["assessment"] for item in attempts[:3]], ["correct"] * 3)
        self.assertEqual(attempts[3]["assessment"], "needs_review")

        # Selecting a different sentence on the same page does not satisfy a question's authored evidence.
        reference_lesson = public["lessons"][1]
        expected_evidence = self._student_evidence(
            reference_lesson, required_refs_by_lesson[reference_lesson["lessonId"]],
        )
        wrong_evidence = next((option for option in reference_lesson["evidenceOptions"]
            if option.get("page") == expected_evidence[0].get("page")
            and option.get("sentenceId")
            and option.get("sentenceId") != expected_evidence[0].get("sentenceId")), None)
        self.assertIsNotNone(wrong_evidence, "synthetic reference page must expose a distinct sentence option")
        wrong_reference = self.client.post(f"/api/chapters/{chapter_id}/attempts", json={
            "attemptId": "chapter-english-wrong-reference-evidence",
            "publicationId": public["publicationId"],
            "lessonId": reference_lesson["lessonId"], "questionId": reference_lesson["lessonId"],
            "learnerId": "chapter-shared-learner", "answer": {"text": "the volunteers"},
            "evidenceRefs": [wrong_evidence],
        })
        self.assertEqual(wrong_reference.status_code, 200, wrong_reference.text)
        self.assertEqual(wrong_reference.json()["assessment"], "needs_review")

        # Then a real teacher decision survives an AppStore reload and English attempts do not alter math mastery
        reviewed = self.client.patch(
            f"/api/chapters/{chapter_id}/attempts/{attempts[3]['attemptId']}/review",
            json={"reviewer": "teacher-english-review", "decision": "correct", "note": "Source-supported paraphrase."},
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        reloaded_store, reloaded_client = self._reloaded_client()
        restored = reloaded_client.get(
            f"/api/chapters/{chapter_id}/attempts/{attempts[3]['attemptId']}",
            params={"learnerId": "chapter-shared-learner"},
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["assessment"], "correct")
        self.assertEqual(restored.json()["reviews"][-1]["reviewer"], "teacher-english-review")
        self.assertEqual(restored.json()["feedback"]["evidence"]["assessment"], "needs_review")
        self.assertEqual(reloaded_store.list_mastery("chapter-shared-learner"), math_mastery_before)

    def test_user_generates_a_chapter_concurrently_then_both_responses_share_one_current_lesson_set(self) -> None:
        # Given a chapter source that has no generated lesson set yet
        chapter = self._create_chapter("math", "并发生成", [{
            "page": 1, "text": "一次函数 y=2x+1，当 x=1 时求 y。",
        }])
        chapter_id = chapter["chapterId"]
        gate = Barrier(2)

        # When two independent clients generate that chapter at the same time
        def generate() -> tuple[int, dict[str, Any]]:
            store, client = self._temporary_client()
            try:
                gate.wait()
                response = client.post(f"/api/chapters/{chapter_id}/generate")
                return response.status_code, response.json()
            finally:
                self._close_temporary_client(store, client)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: generate(), range(2)))

        # Then both clients observe the same persisted lesson set with no orphan lessons
        self.assertEqual([status for status, _ in results], [200, 200])
        returned_sets = [{lesson["lessonId"] for lesson in body["lessons"]} for _, body in results]
        self.assertEqual(returned_sets[0], returned_sets[1])
        persisted = self.service.get(chapter_id)
        self.assertEqual(set(persisted["currentLessonIds"]), returned_sets[0])
        self.assertEqual({lesson["lessonId"] for lesson in self.store.list_lessons()}, returned_sets[0])

    def test_user_publishes_concurrently_then_one_immutable_publication_is_reused(self) -> None:
        # Given an authored, approved lesson that has not yet been published
        chapter = self._create_chapter("math", "并发发布", [{
            "page": 1, "text": "一次函数 y=2x+1，当 x=1 时求 y。",
        }])
        chapter_id = chapter["chapterId"]
        generated = self.client.post(f"/api/chapters/{chapter_id}/generate").json()
        lesson = generated["lessons"][0]
        revision_id = chapter["sourceRevisions"][-1]["sourceRevisionId"]
        edit = self.client.put(f"/api/chapters/{chapter_id}/lessons/{lesson['lessonId']}", json={
            "prompt": "当 x=1 时求 y。", "answer": "3", "answerType": "numeric",
            "sourceRevisionId": revision_id, "page": 1,
            "expectedRecordVersion": self._record_version(chapter_id),
        })
        self.assertEqual(edit.status_code, 200, edit.text)
        review = self._approve_lesson(chapter_id, lesson["lessonId"], "teacher-concurrent-publish")
        self.assertEqual(review.status_code, 200, review.text)
        gate = Barrier(2)

        # When two independent clients publish at the same time
        def publish() -> tuple[int, dict[str, Any]]:
            store, client = self._temporary_client()
            try:
                gate.wait()
                response = client.post(f"/api/chapters/{chapter_id}/publish")
                return response.status_code, response.json()
            finally:
                self._close_temporary_client(store, client)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: publish(), range(2)))

        # Then both responses name one publication, and persistence contains only that published version
        self.assertEqual([status for status, _ in results], [201, 201])
        publication_ids = {body["publicationId"] for _, body in results}
        self.assertEqual(len(publication_ids), 1)
        chapter_after = self.service.get(chapter_id)
        self.assertEqual(len(chapter_after["publications"]), 1)
        self.assertEqual({item["publicationId"] for item in self.store.list_publications("published")}, publication_ids)

    def test_user_replays_the_same_attempt_concurrently_then_learning_evidence_is_added_once(self) -> None:
        # Given a published math question and a stable request with a correct source citation
        chapter = self._create_chapter("math", "并发作答", [{
            "page": 1, "text": "一次函数 y=2x+1，当 x=1 时求 y。",
        }])
        publication, lesson = self._generate_edit_approve_publish_math(chapter)
        request = {
            "attemptId": "chapter-concurrent-attempt", "publicationId": publication["publicationId"],
            "lessonId": lesson["lessonId"], "questionId": lesson["lessonId"],
            "learnerId": "chapter-concurrent-learner", "answer": {"numericAnswer": "3"},
            "evidenceRefs": self._student_evidence(lesson),
        }
        gate = Barrier(2)

        # When two independent clients deliver the same attempt concurrently
        def submit() -> tuple[int, dict[str, Any]]:
            store, client = self._temporary_client()
            try:
                gate.wait()
                response = client.post(f"/api/chapters/{chapter['chapterId']}/attempts", json=request)
                return response.status_code, response.json()
            finally:
                self._close_temporary_client(store, client)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: submit(), range(2)))

        # Then both callers see the same saved result and only one learning event is recorded
        self.assertEqual([status for status, _ in results], [200, 200])
        self.assertEqual({body["assessment"] for _, body in results}, {"correct"})
        mastery = self.store.list_mastery("chapter-concurrent-learner")
        self.assertEqual(len(mastery), 1)
        self.assertEqual(mastery[0]["attemptCount"], 1)
        self.assertEqual(mastery[0]["evidenceCount"], 1)

    def test_user_replays_an_english_attempt_concurrently_then_one_source_bound_attempt_is_saved(self) -> None:
        # Given a reviewed English answer and a matching sentence citation from a persisted source revision
        chapter = self._create_chapter("english", "Concurrent synthetic reading", [{
            "page": 1, "text": "Mina moved to Boston last year.",
        }])
        chapter_id = chapter["chapterId"]
        generated = self.client.post(f"/api/chapters/{chapter_id}/generate")
        self.assertEqual(generated.status_code, 200, generated.text)
        lesson = generated.json()["lessons"][0]
        question = lesson["questionPayload"]["question"]
        source_revision_id = chapter["sourceRevisions"][-1]["sourceRevisionId"]
        edit = self.client.put(f"/api/chapters/{chapter_id}/lessons/{lesson['lessonId']}", json={
            "prompt": "Where did Mina move?", "answer": "Boston", "answerType": "text",
            "questionKind": "explicit", "answerMode": "objective", "acceptedAnswers": ["Boston"],
            "requiredEvidenceRefs": question["requiredEvidenceRefs"],
            "rubric": {"supportStatus": "supported"},
            "sourceRevisionId": source_revision_id, "page": 1,
            "expectedRecordVersion": self._record_version(chapter_id),
        })
        self.assertEqual(edit.status_code, 200, edit.text)
        self.assertEqual(self._approve_lesson(chapter_id, lesson["lessonId"], "teacher-english-concurrent").status_code, 200)
        publication = self.client.post(f"/api/chapters/{chapter_id}/publish")
        self.assertEqual(publication.status_code, 201, publication.text)
        public = self.client.get(f"/api/chapters/{chapter_id}/published")
        self.assertEqual(public.status_code, 200, public.text)
        public_lesson = public.json()["lessons"][0]
        request = {
            "attemptId": "chapter-english-concurrent-attempt",
            "publicationId": publication.json()["publicationId"],
            "lessonId": lesson["lessonId"], "questionId": lesson["lessonId"],
            "learnerId": "chapter-english-concurrent-learner", "answer": {"text": "Boston"},
            "evidenceRefs": self._student_evidence(public_lesson, question["requiredEvidenceRefs"]),
        }
        gate = Barrier(2)

        # When two independent clients submit the same English attempt at the same time
        def submit() -> tuple[int, dict[str, Any]]:
            store, client = self._temporary_client()
            try:
                gate.wait()
                response = client.post(f"/api/chapters/{chapter_id}/attempts", json=request)
                return response.status_code, response.json()
            finally:
                self._close_temporary_client(store, client)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: submit(), range(2)))

        # Then both callers receive the same answer judgment backed by one durable English attempt
        self.assertEqual([status for status, _ in results], [200, 200])
        self.assertEqual({body["assessment"] for _, body in results}, {"correct"})
        stored = self.store.get_chapter_attempt(request["attemptId"])
        self.assertIsNotNone(stored)
        self.assertEqual(stored["subject"], "english")

        # Reusing the attempt ID with changed answer, evidence, or learner is explicitly rejected.
        collisions = [
            {**request, "answer": {"text": "Cambridge"}},
            {**request, "evidenceRefs": [{**request["evidenceRefs"][0], "quote": "Mina moved"}]},
            {**request, "learnerId": "different-english-learner"},
        ]
        for index, collided in enumerate(collisions):
            with self.subTest(collision=index):
                response = self.client.post(f"/api/chapters/{chapter_id}/attempts", json=collided)
                self.assertIn(response.status_code, {404, 409}, response.text)

    def test_user_edits_while_the_lesson_is_reviewed_then_stale_approval_cannot_survive(self) -> None:
        # Given an approved question that a teacher changes while another review request is in flight
        chapter = self._create_chapter("math", "并发编辑审核", [{
            "page": 1, "text": "一次函数 y=2x+1，当 x=1 时求 y。",
        }])
        chapter_id = chapter["chapterId"]
        generated = self.client.post(f"/api/chapters/{chapter_id}/generate").json()
        lesson = generated["lessons"][0]
        revision_id = chapter["sourceRevisions"][-1]["sourceRevisionId"]
        initial = {"prompt": "当 x=1 时求 y。", "answer": "3", "answerType": "numeric", "sourceRevisionId": revision_id, "page": 1,
                   "expectedRecordVersion": self._record_version(chapter_id)}
        self.assertEqual(self.client.put(f"/api/chapters/{chapter_id}/lessons/{lesson['lessonId']}", json=initial).status_code, 200)
        self.assertEqual(self._approve_lesson(chapter_id, lesson["lessonId"], "teacher-old-approval").status_code, 200)
        stale_record_version = self.client.get(f"/api/chapters/{chapter_id}").json()["recordVersion"]
        changed = {**initial, "answer": "4", "prompt": "当 x=1 时，改版题要求 y 等于多少？",
                   "expectedRecordVersion": stale_record_version}
        gate = Barrier(2)

        # When the concurrent operations begin from separate database-backed clients
        def edit() -> int:
            store, client = self._temporary_client()
            try:
                gate.wait()
                return client.put(f"/api/chapters/{chapter_id}/lessons/{lesson['lessonId']}", json=changed).status_code
            finally:
                self._close_temporary_client(store, client)

        def review() -> int:
            store, client = self._temporary_client()
            try:
                gate.wait()
                return client.patch(f"/api/chapters/{chapter_id}/lessons/{lesson['lessonId']}/review", json={
                    "decision": "approve", "reviewer": "teacher-concurrent-review",
                    "expectedRecordVersion": stale_record_version,
                }).status_code
            finally:
                self._close_temporary_client(store, client)

        with ThreadPoolExecutor(max_workers=2) as executor:
            edit_future = executor.submit(edit)
            review_future = executor.submit(review)
            statuses = (edit_future.result(), review_future.result())

        # Then the new answer survives and the older review event is not carried onto that version
        self.assertEqual(sorted(statuses), [200, 409], statuses)
        if statuses[0] == 409:
            # A review may legitimately finish first; refresh before retrying the edit.
            changed["expectedRecordVersion"] = self._record_version(chapter_id)
            self.assertEqual(self.client.put(
                f"/api/chapters/{chapter_id}/lessons/{lesson['lessonId']}", json=changed,
            ).status_code, 200)
        self.assertEqual(self.client.patch(
            f"/api/chapters/{chapter_id}/lessons/{lesson['lessonId']}/review", json={
                "decision": "approve", "reviewer": "teacher-stale-review",
                "expectedRecordVersion": stale_record_version,
            },
        ).status_code, 409)
        current = self.store.load_lesson(lesson["lessonId"])
        current_answer = current["questionPayload"]["question"]["answerSpec"]["expected"]
        self.assertEqual(current_answer, "4")
        self.assertEqual(current["status"], "in_review")
        self.assertEqual(self.client.post(f"/api/chapters/{chapter_id}/publish").status_code, 409)
        self.assertNotIn("teacher-old-approval", {review["reviewer"] for review in current.get("reviews", [])})
