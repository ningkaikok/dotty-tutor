"""User acceptance for recoverable, reviewed textbook courseware on PostgreSQL."""

from __future__ import annotations

import json
import unittest
from typing import Any

from pypdf import PdfWriter
from sqlalchemy import func, select

from application.courseware_jobs import build_courseware_registry
from application.job_worker import JobWorker
from application.services.chapter_courses import ChapterCourseService
from application.services.courseware import CoursewareService
from domain.chapters.source import pages_from_ocr
from domain.contracts.courseware import (
    CoursewareActivityBlock,
    CoursewareAttemptRequest,
    CoursewareDocument,
    CoursewarePageContent,
    CoursewarePlanCreate,
    CoursewareTextBlock,
)
from persistence.app_store import AppStore
from persistence.courseware_store import CoursewareStore, courseware_attempts
from persistence.job_store import JobStore
from tests.postgres_test_support import PostgresTestCase


class DeterministicCoursewareRuntime:
    """A deterministic model-boundary implementation for PostgreSQL acceptance tests."""

    def __init__(self, refs: dict[int, dict[str, Any]], revision_id: str) -> None:
        self.refs = refs
        self.revision_id = revision_id
        self.fail_second_page_once = True

    @staticmethod
    def _run_metadata() -> dict[str, Any]:
        return {
            "provider": "acceptance-runtime", "model": "fixture-v1",
            "actualProvider": "acceptance-runtime", "actualModel": "fixture-v1",
            "usage": {"promptTokens": 10, "outputTokens": 10}, "durationMs": 1,
        }

    def generate_json(self, prompt: str, schema: dict[str, Any], *, task: str, **kwargs: Any):
        del schema, kwargs
        if task == "courseware_plan_generation":
            first, second = self.refs[23], self.refs[24]
            objective = {
                "objectiveId": "objective-critical-reading",
                "description": "Use a cited passage to explain one critical reading choice.",
                "sourceRefs": [first],
            }
            return {
                "objectives": [objective],
                "sessions": [{
                    "sessionId": "session-opening", "title": "Opening reading workshop",
                    "durationMinutes": 10,
                    "sections": [{
                        "sectionId": "section-reading", "title": "Read and respond",
                        "purpose": "Build an evidence-backed response.", "estimatedMinutes": 10,
                        "pages": [
                            {"pageId": "page-opening", "title": "Notice the claim", "pageKind": "reading",
                             "layout": "text_focus", "objectiveIds": [objective["objectiveId"]],
                             "sourceRefs": [first], "estimatedMinutes": 5},
                            {"pageId": "page-response", "title": "Critical response", "pageKind": "activity",
                             "layout": "figure_focus", "objectiveIds": [objective["objectiveId"]],
                             "sourceRefs": [second], "estimatedMinutes": 5},
                        ],
                    }],
                }],
                "coverageMap": {objective["objectiveId"]: [first]},
            }, self._run_metadata()

        outline_start = prompt.index("Page outline: ") + len("Page outline: ")
        outline_end = prompt.index("\nObjectives:", outline_start)
        outline = json.loads(prompt[outline_start:outline_end])
        page_id = outline["pageId"]
        source_page = 23 if page_id == "page-opening" else 24
        if page_id == "page-response" and self.fail_second_page_once:
            self.fail_second_page_once = False
            raise TimeoutError("temporary model boundary failure")
        ref = self.refs[source_page]
        blocks: list[dict[str, Any]] = [{
            "kind": "text", "blockId": f"{page_id}-text",
            "text": f"Discuss the evidence on physical page {source_page}.",
            "sourceRefs": [ref], "authorship": "teacher_authored",
        }]
        if page_id == "page-opening":
            asset_id = f"source-page-{self.revision_id[:12]}-{source_page}"
            if asset_id not in prompt:
                raise AssertionError("prompt did not receive its page-scoped controlled asset catalogue")
            blocks.append({"kind": "image", "blockId": "page-opening-source-image",
                           "assetId": asset_id, "alt": "Textbook source page 23",
                           "caption": "Source page 23 preview"})
        return {
            "blocks": blocks,
            "teacherNotes": "Private teacher facilitation note.",
            "revealSteps": [{"revealId": f"{page_id}-reveal", "label": "Facilitator note",
                             "content": "Invite learners to point to the cited sentence.", "sourceRefs": [ref]}],
        }, self._run_metadata()


class CoursewarePostgresAcceptanceTests(PostgresTestCase):
    def test_user_submits_a_single_blank_then_the_attempt_is_saved_with_objective_feedback(self) -> None:
        # Given an immutable classroom activity with one reviewed blank
        cid, pid = "blank-courseware", "blank-publication"
        chapter = self.chapters.create({"title": "Blank fixture", "subject": "english", "teachingMode": "tutorial",
            "source": {"pageStart": 1, "pageEnd": 1, "pages": [{"page": 1, "text": "Name the audience."}]}})
        self.courseware_store.save_record(
            record_id=f"document:{cid}", record_type="document", chapter_id=chapter["chapterId"],
            courseware_id=cid, source_revision_id="blank-source", status="published",
            payload={"coursewareId": cid, "status": "published"}, expected_version=0,
        )
        self.courseware_store.save_publication({
            "publicationId": pid, "coursewareId": cid, "version": 1,
            "sourceRevisionId": "blank-source", "sourceTitle": "Engineering fixture",
            "sourceUrl": None, "attributionText": "Local fixture", "distributionScope": "classroom",
            "snapshot": {"chapterId": chapter["chapterId"], "pages": [{"blocks": [{"kind": "activity", "blockId": "blank",
                "activityId": "name-audience", "activityKind": "fill_blank", "assessmentMode": "objective",
                "instructions": "Name the audience", "answerKey": "audience"}]}]},
            "reviewRecords": [], "publishedAt": 1_800_000_000.0,
        })
        service = CoursewareService(store=self.courseware_store, chapters=self.chapters, jobs=self.jobs)
        # When the player sends its text field twice with the same command
        request = CoursewareAttemptRequest(publicationId=pid, activityId="name-audience",
            commandId="blank-command", response={"text": "audience"})
        first = service.attempt(cid, learner_id="fixture-learner", request=request)
        second = service.attempt(cid, learner_id="fixture-learner", request=request)
        # Then feedback is correct and the immutable attempt is not duplicated
        self.assertEqual(first["assessment"], "correct")
        self.assertEqual(first["attemptId"], second["attemptId"])

    def setUp(self) -> None:
        super().setUp()
        self.app_store = AppStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.app_store.close)
        self.jobs = JobStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.jobs.close)
        self.courseware_store = CoursewareStore(engine=self.app_store.engine)
        self.chapters = ChapterCourseService(self.app_store, self.jobs)

    def test_user_restores_an_archived_draft_twice_then_teacher_edits_and_pending_reviews_are_preserved(self) -> None:
        # Given a source-bound engineering draft without teacher approvals
        from application.services.courseware_restore import restore_courseware_draft
        chapter, source, _refs, service = self._source_course()
        service.create_plan(chapter["chapterId"], CoursewarePlanCreate(
            subject="english", audience="Readers", languagePolicy="English", durationMinutes=10,
            objectives=["Read cited evidence"], expectedRecordVersion=1, pageStart=23, pageEnd=24,
        ))
        JobWorker(self.jobs, build_courseware_registry(service), worker_id="restore-test").run_once()
        plan = service.get_plan(chapter["chapterId"])
        approved = service.approve_plan(chapter["chapterId"], plan_version=plan["planVersion"], expected_record_version=plan["recordVersion"], command_id="test-create-draft")
        document = approved["documents"][0]
        packet = {"title": chapter["title"], "sourceFileSha256": source["sourceFileSha256"],
                  "pageStart": 23, "pageEnd": 25, "pages": source["pages"]}
        # When the original archive is restored, a new pending draft is created and existing source history stays intact
        restored = restore_courseware_draft(service, packet, plan, document, upload_id=source["uploadId"])
        draft = service.get_document(restored["coursewareId"])
        self.assertIsNone(draft["publicationId"])
        self.assertEqual(draft["reviewRecords"], [])
        self.assertTrue(all(page["reviewStatus"] == "pending" for page in draft["pages"]))
        draft["title"] = "Teacher retained title"
        self.courseware_store.save_record(record_id=f"document:{restored['coursewareId']}", record_type="document",
            chapter_id=restored["chapterId"], courseware_id=restored["coursewareId"], source_revision_id=source["sourceRevisionId"],
            payload=draft, status="draft", expected_version=draft["recordVersion"])
        # Then a repeated restore returns the same record without overwriting teacher changes
        repeated = restore_courseware_draft(service, packet, plan, document, upload_id=source["uploadId"])
        self.assertFalse(repeated["created"])
        self.assertEqual(service.get_document(restored["coursewareId"])["title"], "Teacher retained title")
        self.assertEqual(self.app_store.load_chapter(chapter["chapterId"])["title"], chapter["title"])
        from application.services.quality_review_courseware import (
            courseware_review_tasks,
        )
        tasks = courseware_review_tasks(self.app_store, self.courseware_store, chapter_id=restored["chapterId"], upload_id=None)
        self.assertEqual([item["coursewareId"] for item in tasks], [restored["coursewareId"]])
        self.assertEqual(tasks[0]["pendingPageCount"], 2)
        self.assertEqual(courseware_review_tasks(self.app_store, self.courseware_store, chapter_id=restored["chapterId"], upload_id="foreign-upload"), [])
        incomplete = self.app_store.load_job(source["uploadId"])
        incomplete.update(uploadId="completed-with-json-null", result=None)
        self.app_store.save_job(incomplete)
        self.assertNotIn("completed-with-json-null", {item["uploadId"] for item in self.app_store.list_imports()})
        self.assertIn(source["uploadId"], {item["uploadId"] for item in self.app_store.list_imports()})

    def _source_course(self, *, distribution_scope: str = "classroom"):
        upload_id = "courseware-acceptance-book"
        directory = self.app_store.upload_root / upload_id
        directory.mkdir(parents=True)
        writer = PdfWriter()
        for _ in range(25):
            writer.add_blank_page(width=200, height=300)
        writer.write(directory / "source.pdf")
        source_text = (
            "<!-- page 23 -->\nThe Digital World helps readers build on what they know. "
            "Readers respond critically to evidence.\n"
            "<!-- page 24 -->\nA second cited passage supports a thoughtful response.\n"
            "<!-- page 25 -->\nThis page is outside the planned first lesson.\n"
        )
        source_path = directory / "source.pdf"
        self.app_store.save_job({
            "uploadId": upload_id, "filename": "Writing Guide with Handbook.pdf",
            "contentType": "application/pdf", "size": source_path.stat().st_size,
            "chunkSize": 1024, "totalChunks": 1, "directory": directory,
            "status": "complete", "sourceText": source_text,
            "result": {"materialKind": "textbook", "extraction": {"pageCount": 25}},
        })
        chapter = self.chapters.create({
            "title": "The Digital World", "subject": "english", "teachingMode": "tutorial",
            "source": {
                "uploadId": upload_id, "sourceVersion": "acceptance-source-v1",
                "sourceTitle": "Writing Guide with Handbook", "license": "CC BY-NC-SA 4.0",
                "attributionText": "Access for free at openstax.org.",
                "distributionScope": distribution_scope, "pageStart": 23, "pageEnd": 25,
                "pages": pages_from_ocr(source_text),
            },
        })
        source = chapter["sourceRevisions"][-1]
        refs = {}
        for page in source["pages"]:
            sentence = page["sentences"][0]
            refs[int(page["page"])] = {
                "sourceRevisionId": source["sourceRevisionId"],
                "page": int(page["page"]), "sentenceIds": [sentence["sentenceId"]],
                "regionIds": [], "assetId": None,
            }
        runtime = DeterministicCoursewareRuntime(refs, source["sourceRevisionId"])
        service = CoursewareService(
            store=self.courseware_store, chapters=self.chapters, jobs=self.jobs, runtime=runtime,
        )
        return chapter, source, refs, service

    def test_user_confirms_a_region_heavy_session_then_receives_an_actionable_limit_without_partial_drafts(self) -> None:
        # Given two valid physical pages with 100 saved regions each
        chapter, source, _refs, service = self._source_course()
        for page in source["pages"][:2]:
            page["regions"] = [{"regionId": f"region-{index}", "x": .1, "y": .1, "width": .1, "height": .1}
                               for index in range(100)]
        self.app_store.save_chapter(chapter)
        service.create_plan(chapter["chapterId"], CoursewarePlanCreate(
            subject="english", audience="Readers", languagePolicy="English", durationMinutes=10,
            objectives=["Read cited evidence"], expectedRecordVersion=chapter["recordVersion"], pageStart=23, pageEnd=24,
        ))
        JobWorker(self.jobs, build_courseware_registry(service), worker_id="region-limit-test").run_once()
        plan = service.get_plan(chapter["chapterId"])
        # When confirming the session, Then explain the limit and retain the editable plan atomically
        with self.assertRaisesRegex(ValueError, "拆分课时"):
            service.approve_plan(chapter["chapterId"], plan_version=plan["planVersion"],
                expected_record_version=plan["recordVersion"], command_id="region-limit")
        self.assertEqual(service.get_plan(chapter["chapterId"])["status"], "awaiting_confirmation")
        self.assertEqual(self.courseware_store.list_documents(chapter["chapterId"]), [])

    def test_user_submits_formula_ocr_without_quality_flags_then_source_still_requires_review(self) -> None:
        # Given a client omits quality flags for damaged formula OCR
        chapter = self.chapters.create({"title": "Damaged formula", "subject": "math", "teachingMode": "tutorial",
            "source": {"pageStart": 1, "pageEnd": 1,
                       "pages": [{"page": 1, "text": r"$$ - 1 3 \frac { 7 } { 3 } = - 1 3 6 $$"}]}})
        # When source normalization runs, Then the server preserves the text and blocks generation
        source = chapter["sourceRevisions"][-1]
        self.assertIn("unreadable", source["pages"][0]["flags"])
        with self.assertRaisesRegex(ValueError, "来源未就绪"):
            CoursewareService._selected_pages(source)

    def test_user_resubmits_a_failed_plan_with_a_new_command_then_a_new_version_can_complete(self) -> None:
        # Given a valid textbook source and a terminal failure at the external model boundary
        chapter, _source, refs, service = self._source_course()

        class RejectPlanRuntime(DeterministicCoursewareRuntime):
            def generate_json(self, *args: Any, **kwargs: Any):
                raise ValueError("unusable model output")

        service.runtime = RejectPlanRuntime(refs, _source["sourceRevisionId"])
        request = CoursewarePlanCreate(
            subject="english", audience="Readers", languagePolicy="English",
            durationMinutes=10, objectives=["Use source evidence"],
            expectedRecordVersion=chapter["recordVersion"], pageStart=23, pageEnd=24,
        )
        first = service.create_plan(chapter["chapterId"], request, idempotency_key="failed-command")
        worker = JobWorker(self.jobs, build_courseware_registry(service), worker_id="failed-plan-worker")
        self.assertEqual(worker.run_once()["status"], "failed")
        # When the old command is retried it stays idempotent, while a new command creates a fresh version
        repeated = service.create_plan(chapter["chapterId"], request, idempotency_key="failed-command")
        self.assertEqual(repeated["job"]["jobId"], first["job"]["jobId"])
        service.runtime = DeterministicCoursewareRuntime(refs, _source["sourceRevisionId"])
        replacement = service.create_plan(chapter["chapterId"], request, idempotency_key="new-command")
        self.assertNotEqual(replacement["job"]["jobId"], first["job"]["jobId"])
        self.assertEqual(worker.run_once()["status"], "succeeded")
        # Then the current plan is usable for explicit confirmation, with preserved source provenance
        current = service.get_plan(chapter["chapterId"])
        self.assertEqual(current["planVersion"], first["plan"]["planVersion"] + 1)
        self.assertEqual(current["status"], "awaiting_confirmation")
        self.assertEqual(current["sourceRevisionId"], _source["sourceRevisionId"])

    def test_user_recovers_a_partially_generated_lesson_then_reviews_and_publishes_only_cited_scope(self) -> None:
        # Given a real page-addressed English textbook source and a selected 23–24 opening lesson
        chapter, source, refs, service = self._source_course()
        plan_result = service.create_plan(chapter["chapterId"], CoursewarePlanCreate(
            subject="english", audience="High school readers", languagePolicy="English with cited vocabulary support",
            durationMinutes=10, objectives=["Use passage evidence in a critical response."],
            expectedRecordVersion=chapter["recordVersion"], pageStart=23, pageEnd=24,
        ), idempotency_key="acceptance-plan-opening")
        worker = JobWorker(
            self.jobs, build_courseware_registry(service), worker_id="courseware-acceptance-worker",
        )
        first_plan_job = worker.run_once()
        self.assertEqual(first_plan_job["status"], "succeeded")
        plan = service.get_plan(chapter["chapterId"])
        approved = service.approve_plan(
            chapter["chapterId"], plan_version=plan["planVersion"],
            expected_record_version=plan["recordVersion"], command_id="confirm-opening-v1",
        )
        courseware_id = approved["documents"][0]["coursewareId"]

        # When one page checkpoint succeeds and the next model call temporarily fails, Worker retry resumes the same draft
        failed_attempt = worker.run_once()
        self.assertEqual(failed_attempt["status"], "queued")
        partial = service.get_document(courseware_id)
        self.assertEqual(partial["pages"][0]["generationStatus"], "completed")
        self.assertEqual(partial["pages"][1]["generationStatus"], "failed")
        recovered = worker.run_once()
        self.assertEqual(recovered["status"], "succeeded")
        document = service.get_document(courseware_id)
        self.assertEqual(document["generationStatus"], "completed")
        self.assertEqual([page["version"] for page in document["pages"]], [2, 2])
        self.assertEqual(document["pages"][0]["generationAudit"]["promptId"], "courseware.page.english")
        self.assertTrue(document["pages"][0]["blocks"][1]["assetId"].startswith("source-page-"))
        self.assertEqual(document["sourceLicense"], "CC BY-NC-SA 4.0")

        # Then a teacher cannot cite page 25 outside the frozen source scope.
        out_of_scope = CoursewarePageContent(
            title="Out of scope", pageKind="reading", layout="text_focus",
            sourceRefs=[refs[25]], estimatedMinutes=5,
            blocks=[CoursewareTextBlock(
                kind="text", blockId="out-of-scope-block", text="Unplanned page content.",
                sourceRefs=[refs[25]], authorship="teacher_authored",
            )],
        )
        with self.assertRaisesRegex(ValueError, "不在当前课件范围"):
            service.edit_page(
                courseware_id, "page-opening", content=out_of_scope,
                expected_record_version=document["recordVersion"],
            )

        reviewed = service.review_document(
            courseware_id, decision="approve", page_ids=[], note="Source and structure reviewed.",
            expected_record_version=document["recordVersion"], actor_id="teacher-session-acceptance",
        )
        publication = service.publish(
            courseware_id, expected_record_version=reviewed["recordVersion"],
        )
        student = service.get_published(courseware_id, publication["publicationId"])
        serialized = json.dumps(student, ensure_ascii=False)
        self.assertEqual(student["sourceLicense"], "CC BY-NC-SA 4.0")
        self.assertIn("Access for free at openstax.org.", serialized)
        self.assertEqual(student["status"], "published")
        for page in student["pages"]:
            self.assertNotIn("teacherNotes", page)
            self.assertNotIn("revealSteps", page)
        self.assertFalse(any(block["kind"] == "activity" for page in student["pages"] for block in page["blocks"]))
        self.assertEqual(plan_result["job"]["jobType"], "courseware.plan.generate")

    def test_user_teachers_can_preview_private_published_assets_but_students_cannot(self) -> None:
        # Given a textbook chapter and an immutable publication restricted to its teacher
        chapter, source, refs, service = self._source_course(distribution_scope="private")
        courseware_id = "private-asset-courseware"
        publication_id = "private-asset-publication"
        asset_id = f"source-page-{source['sourceRevisionId'][:12]}-23"
        source_ref = {**refs[23], "assetId": asset_id}
        snapshot = CoursewareDocument.model_validate({
            "coursewareId": courseware_id, "chapterId": chapter["chapterId"], "planVersion": 1,
            "sourceRevisionId": source["sourceRevisionId"], "pageStart": 23, "pageEnd": 23,
            "sourceTitle": "Writing Guide with Handbook", "sourceLicense": "CC BY-NC-SA 4.0",
            "attributionText": "Access for free at openstax.org.", "sourceDistributionScope": "private",
            "subject": "english", "audience": "Teacher preview", "languagePolicy": "English",
            "title": "Private source preview", "durationMinutes": 10,
            "sections": [{"sectionId": "section-private", "title": "Source page", "estimatedMinutes": 10,
                          "pageIds": ["page-private"]}],
            "pages": [{
                "pageId": "page-private", "sectionId": "section-private", "pageKind": "reading",
                "layout": "figure_focus", "title": "Review source page", "sourceRefs": [source_ref],
                "blocks": [{"kind": "image", "blockId": "private-image-block", "assetId": asset_id,
                            "alt": "Source page 23"}],
                "teacherNotes": "Review the source image.", "revealSteps": [], "estimatedMinutes": 10,
                "generationStatus": "completed", "reviewStatus": "approved", "version": 1,
            }],
            "assets": [{
                "assetId": asset_id, "kind": "source_page", "origin": "textbook", "sourceRefs": [source_ref],
                "license": "CC BY-NC-SA 4.0", "attributionText": "Access for free at openstax.org.",
                "distributionScope": "private", "alt": "Source page 23", "status": "available",
            }],
            "reviewIssues": [], "recordVersion": 1, "status": "published", "generationStatus": "completed",
            "sourceReadiness": "ready", "reviewStatus": "ready", "publicationId": publication_id,
            "generationJobId": None, "reviewRecords": [], "createdAt": 1_800_000_000.0,
            "updatedAt": 1_800_000_000.0,
        }).model_dump()
        self.courseware_store.save_record(
            record_id=f"document:{courseware_id}", record_type="document", chapter_id=chapter["chapterId"],
            courseware_id=courseware_id, source_revision_id=source["sourceRevisionId"],
            payload={"coursewareId": courseware_id}, status="published", expected_version=0,
        )
        self.courseware_store.save_publication({
            "publicationId": publication_id, "coursewareId": courseware_id, "version": 1,
            "sourceRevisionId": source["sourceRevisionId"], "sourceTitle": "Writing Guide with Handbook",
            "sourceUrl": None, "attributionText": "Access for free at openstax.org.",
            "distributionScope": "private", "snapshot": snapshot, "reviewRecords": [],
            "publishedAt": 1_800_000_000.0,
        })

        # When a teacher opens the presentation, the URL is tied to the frozen publication and resolves its source page.
        presentation = service.get_presentation(courseware_id, publication_id)
        self.assertTrue(presentation["document"]["assets"][0]["src"].endswith(f"?publicationId={publication_id}"))
        path, media_type = service.get_chapter_assets(
            courseware_id, asset_id, publication_id=publication_id, teacher_preview=True,
        )
        self.assertEqual(media_type, "image/png")
        self.assertTrue(path.endswith(".png"))

        # Then a student-facing request for the same private publication asset remains indistinguishable from missing.
        with self.assertRaisesRegex(LookupError, "课件素材不存在"):
            service.get_chapter_assets(courseware_id, asset_id, publication_id=publication_id)

        # When the chapter is withdrawn, Then even the old teacher preview cannot deliver the preserved source asset.
        chapter["deletedAt"] = 1_800_000_001.0
        self.app_store.save_chapter(chapter)
        with self.assertRaises(LookupError):
            service.get_chapter_assets(courseware_id, asset_id, publication_id=publication_id, teacher_preview=True)

    def test_user_submits_multiple_choice_options_then_the_persisted_attempt_is_correct_and_idempotent(self) -> None:
        # Given an immutable classroom publication with a two-option objective activity
        chapter = self.chapters.create({"title": "Choice fixture", "subject": "english", "teachingMode": "tutorial",
            "source": {"pageStart": 1, "pageEnd": 1, "pages": [{"page": 1, "text": "Select the evidence."}]}})
        courseware_id = "attempt-courseware"
        publication_id = "attempt-publication"
        activity = {
            "kind": "activity", "blockId": "choice-block", "activityId": "choose-evidence",
            "activityKind": "choice", "instructions": "Select both supported observations.",
            "assessmentMode": "objective", "choices": ["A", "B", "C"],
            "answerKey": ["A", "B"], "acceptedAnswers": [], "teacherRubric": "private",
        }
        reference = {
            "sourceRevisionId": "attempt-source", "page": 1,
            "sentenceIds": ["attempt-sentence"], "regionIds": [], "assetId": None,
        }
        snapshot = CoursewareDocument.model_validate({
            "coursewareId": courseware_id, "chapterId": chapter["chapterId"], "planVersion": 1,
            "sourceRevisionId": "attempt-source", "pageStart": 1, "pageEnd": 1,
            "sourceTitle": "Textbook", "sourceLicense": "CC BY 4.0",
            "attributionText": "Source attribution", "sourceDistributionScope": "classroom",
            "subject": "english", "audience": "Learners", "languagePolicy": "English",
            "title": "Evidence choices", "durationMinutes": 10,
            "sections": [{"sectionId": "section-choice", "title": "Choose", "estimatedMinutes": 10,
                          "pageIds": ["page-choice"]}],
            "pages": [{
                "pageId": "page-choice", "sectionId": "section-choice", "pageKind": "activity",
                "layout": "text_focus", "title": "Select the evidence", "sourceRefs": [reference],
                "blocks": [CoursewareActivityBlock.model_validate({**activity, "sourceRefs": [reference]}).model_dump()],
                "teacherNotes": "Private explanation", "revealSteps": [], "estimatedMinutes": 10,
                "generationStatus": "completed", "reviewStatus": "approved", "version": 1,
            }],
            "assets": [], "reviewIssues": [], "recordVersion": 1, "status": "published",
            "generationStatus": "completed", "sourceReadiness": "ready", "reviewStatus": "ready",
            "publicationId": publication_id, "generationJobId": None, "reviewRecords": [],
            "createdAt": 1_800_000_000.0, "updatedAt": 1_800_000_000.0,
        }).model_dump()
        self.courseware_store.save_record(
            record_id=f"document:{courseware_id}", record_type="document", chapter_id=chapter["chapterId"],
            courseware_id=courseware_id, source_revision_id="attempt-source", payload={"coursewareId": courseware_id},
            status="published", expected_version=0,
        )
        self.courseware_store.save_publication({
            "publicationId": publication_id, "coursewareId": courseware_id,
            "version": 1, "sourceRevisionId": "attempt-source", "sourceTitle": "Textbook",
            "sourceUrl": None, "attributionText": "Source attribution", "distributionScope": "classroom",
            "snapshot": snapshot, "reviewRecords": [], "publishedAt": 1_800_000_000.0,
        })
        service = CoursewareService(
            store=self.courseware_store, chapters=self.chapters, jobs=self.jobs,
        )
        student_projection = service.get_published(courseware_id, publication_id)
        public_block = student_projection["pages"][0]["blocks"][0]
        self.assertEqual(public_block["selectionMode"], "multiple")
        self.assertNotIn("answerKey", public_block)
        self.assertNotIn("acceptedAnswers", public_block)
        self.assertNotIn("teacherRubric", public_block)
        self.assertNotIn("teacherNotes", student_projection["pages"][0])
        request = CoursewareAttemptRequest(
            publicationId=publication_id, activityId="choose-evidence", commandId="attempt-command-1",
            response={"selectedOptions": ["A", "B"]},
        )

        # When the learner retries the same command, persistence returns the same assessment rather than duplicating it.
        saved = service.attempt(courseware_id, learner_id="learner-1", request=request)
        repeated = service.attempt(courseware_id, learner_id="learner-1", request=request)
        self.assertEqual(saved["assessment"], "correct")
        self.assertEqual(saved["attemptId"], repeated["attemptId"])
        with self.app_store.engine.connect() as connection:
            count = connection.execute(select(func.count()).select_from(courseware_attempts).where(
                courseware_attempts.c.learner_id == "learner-1",
            )).scalar_one()
        self.assertEqual(count, 1)

        # Given the parent chapter is deleted, When old delivery or attempt URLs are reused, Then history is retained but access stops.
        chapter["deletedAt"] = 1_800_000_001.0
        self.app_store.save_chapter(chapter)
        with self.assertRaises(LookupError):
            service.get_published(courseware_id, publication_id)
        with self.assertRaises(LookupError):
            service.get_presentation(courseware_id, publication_id)
        with self.assertRaises(LookupError):
            service.attempt(courseware_id, learner_id="learner-1", request=request)
        self.assertIsNotNone(self.courseware_store.load_publication(publication_id))


if __name__ == "__main__":
    unittest.main()
