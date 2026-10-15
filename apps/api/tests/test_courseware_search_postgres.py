"""Published textbook evidence remains scoped across indexing and explanation."""
from __future__ import annotations

from typing import Any

from application.services.chapter_courses import ChapterCourseService
from application.services.courseware_search import CoursewareSearchService
from persistence.app_store import AppStore
from persistence.courseware_store import CoursewareStore
from persistence.job_store import JobStore
from persistence.search_store import TutorSearchStore
from tests import test_courseware_postgres_acceptance as courseware_acceptance
from tests.postgres_test_support import PostgresTestCase


class EvidenceRuntime:
    def generate_json(self, prompt: str, schema: dict[str, Any], **kwargs: Any):
        import json

        packet = json.loads(prompt.split("EVIDENCE_JSON:\n", 1)[1])
        return {"explanation": "依据教材：" + packet[0]["excerpt"],
                "evidenceIds": [packet[0]["documentId"]], "sufficient": True}, {"durationMs": 1}


class CoursewareSearchAcceptanceTests(PostgresTestCase):
    _source_course = courseware_acceptance.CoursewarePostgresAcceptanceTests._source_course

    def setUp(self) -> None:
        super().setUp()
        self.app_store = AppStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.app_store.close)
        self.jobs = JobStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.jobs.close)
        self.courseware_store = CoursewareStore(engine=self.app_store.engine)
        self.chapters = ChapterCourseService(self.app_store, self.jobs)

    def _search_fixture(self, *, distribution_scope: str = "classroom"):
        from application.courseware_jobs import build_courseware_registry
        from application.job_worker import JobWorker
        from domain.contracts.courseware import CoursewarePlanCreate

        chapter, source, refs, courseware = self._source_course(distribution_scope=distribution_scope)
        courseware.runtime.fail_second_page_once = False
        courseware.create_plan(chapter["chapterId"], CoursewarePlanCreate(
            subject="english", audience="Readers", languagePolicy="English", durationMinutes=10,
            objectives=["Read cited evidence"], expectedRecordVersion=chapter["recordVersion"],
            pageStart=23, pageEnd=24, autoGeneratePages=True))
        worker = JobWorker(self.jobs, build_courseware_registry(courseware), worker_id="source-search")
        worker.run_once()
        worker.run_once()
        draft = courseware.get_document(courseware.list_chapter_documents(chapter["chapterId"])["items"][0]["coursewareId"])
        publication = courseware.publish(draft["coursewareId"], expected_record_version=draft["recordVersion"],
                                        confirm_review=True, actor_id="source-reviewer")
        index = TutorSearchStore(engine=self.app_store.engine)
        service = CoursewareSearchService(service=courseware, search_store=index, runtime=EvidenceRuntime())
        return service, index, chapter, source, draft["coursewareId"], publication["publicationId"]

    def test_user_searches_only_reviewed_frozen_text_with_page_evidence(self) -> None:
        """Given a reviewed publication, When searching, Then return original text and a bound source page."""
        service, index, _chapter, source, cid, pid = self._search_fixture()
        result = service.search(cid, pid, "cited")
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual(item["sourceRevisionId"], source["sourceRevisionId"])
        self.assertEqual(item["excerpt"], source["pages"][1]["sentences"][0]["text"])
        self.assertEqual(item["sourcePage"], 24)
        self.assertIn(pid, item["previewUrl"])
        self.assertEqual(service.search(cid, pid, "outside")["items"], [])
        self.assertEqual(service.search(cid, pid, "Why is the cited passage helpful?")["items"][0]["sourcePage"], 24)
        self.assertEqual(index.search(query="cited"), [])

    def test_user_cannot_query_a_foreign_or_withdrawn_publication(self) -> None:
        """Given a built index, When the parent is withdrawn or id differs, Then deny stale evidence."""
        service, _index, chapter, _source, cid, pid = self._search_fixture()
        service.search(cid, pid, "cited")
        with self.assertRaises(LookupError):
            service.search("foreign-courseware", pid, "cited")
        chapter["deletedAt"] = 123
        self.app_store.save_chapter(chapter)
        with self.assertRaises(LookupError):
            service.search(cid, pid, "cited")

    def test_user_receives_cited_explanation_or_explicit_evidence_gap(self) -> None:
        """Given frozen evidence, When asking, Then return valid citations and never invent missing evidence."""
        service, _index, _chapter, _source, cid, pid = self._search_fixture()
        response = service.explain(cid, pid, "cited")
        self.assertEqual(response["status"], "answered")
        self.assertEqual(response["evidence"][0]["sourcePage"], 24)
        self.assertIn(response["evidence"][0]["excerpt"], response["explanation"])
        missing = service.explain(cid, pid, "unfindable")
        self.assertEqual(missing["status"], "insufficient_evidence")
        self.assertEqual(missing["evidence"], [])

    def test_user_cannot_search_private_publication_or_preview_foreign_evidence(self) -> None:
        """Given a private snapshot, When searching, Then neither text nor original-page access is exposed."""
        service, _index, _chapter, _source, cid, pid = self._search_fixture(distribution_scope="private")
        with self.assertRaises(LookupError):
            service.search(cid, pid, "cited")
        with self.assertRaises(LookupError):
            service.preview(cid, pid, "foreign-evidence")

    def test_user_rejects_invented_model_citations(self) -> None:
        """Given a model inventing a citation, When explaining, Then hide its unsupported answer."""
        service, _index, _chapter, _source, cid, pid = self._search_fixture()
        service.runtime = InvalidCitationRuntime()
        response = service.explain(cid, pid, "cited")
        self.assertEqual(response["status"], "insufficient_evidence")
        self.assertNotIn("unsupported answer", response["explanation"])
        self.assertEqual(response["evidence"][0]["sourcePage"], 24)

    def test_user_cannot_receive_answer_after_source_is_withdrawn_during_generation(self) -> None:
        """Given a running explanation, When its chapter is withdrawn, Then do not deliver stale output."""
        service, _index, chapter, _source, cid, pid = self._search_fixture()

        def withdraw() -> None:
            chapter["deletedAt"] = 123
            self.app_store.save_chapter(chapter)

        service.runtime = InvalidCitationRuntime(withdraw=withdraw)
        with self.assertRaises(LookupError):
            service.explain(cid, pid, "cited")

    def test_user_cannot_retrieve_old_text_or_image_after_source_revision(self) -> None:
        """Given an indexed frozen source, When a correction revision is saved, Then deny its old evidence."""
        service, _index, chapter, source, cid, pid = self._search_fixture()
        item = service.search(cid, pid, "cited")["items"][0]
        self.assertTrue(service.preview(cid, pid, item["documentId"]).endswith("rendered-page-0024.png"))
        chapter["sourceRevisions"].append({**source, "sourceRevisionId": "corrected-source-revision"})
        self.app_store.save_chapter(chapter)
        with self.assertRaises(LookupError):
            service.search(cid, pid, "cited")
        with self.assertRaises(LookupError):
            service.preview(cid, pid, item["documentId"])

    def test_user_can_read_a_fixed_historical_publication_when_its_source_is_still_current(self) -> None:
        """Given two immutable publications of one source, When using the older link, Then retain its version."""
        service, _index, _chapter, _source, cid, pid = self._search_fixture()
        publication = self.courseware_store.load_publication(pid)
        self.courseware_store.save_publication({**publication, "publicationId": "newer-publication", "version": 2})
        item = service.search(cid, pid, "cited")["items"][0]
        self.assertEqual(item["publicationId"], pid)
        self.assertEqual(item["publicationVersion"], 1)

    def test_user_receives_original_text_if_model_output_is_not_an_object(self) -> None:
        """Given a malformed model result, When explaining, Then return a safe outage with source evidence."""
        service, _index, _chapter, _source, cid, pid = self._search_fixture()
        service.runtime = NonObjectRuntime()
        result = service.explain(cid, pid, "cited")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["evidence"][0]["sourcePage"], 24)


class InvalidCitationRuntime:
    def __init__(self, *, withdraw: Any = None) -> None:
        self.withdraw = withdraw

    def generate_json(self, prompt: str, schema: dict[str, Any], **kwargs: Any):
        if self.withdraw:
            self.withdraw()
        return {"sufficient": True, "explanation": "unsupported answer", "evidenceIds": ["invented"]}, {}


class NonObjectRuntime:
    def generate_json(self, *args: Any, **kwargs: Any):
        return [], {}
