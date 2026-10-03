"""Real database failure acceptance for the chapter publication transaction."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from application.services.chapter_courses import ChapterCourseService
from persistence.app_store import AppStore
from routers.publication_routes import build_publication_router
from tests.postgres_test_support import PostgresTestCase


class ChapterTransactionAcceptanceTests(PostgresTestCase):
    def test_user_uses_the_legacy_publication_endpoint_then_a_chapter_review_gate_cannot_be_bypassed(self) -> None:
        # Given an unreviewed chapter with a known wrong-figure source problem
        store = AppStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(store.close)
        service = ChapterCourseService(store)
        chapter = service.create({
            "subject": "math", "title": "Flagged original chapter",
            "source": {"license": "Original synthetic content", "pages": [
                {"page": 1, "text": "一次函数 y=2x+1。", "flags": ["wrong_figure"]},
            ]},
        })
        generated = service.generate(chapter["chapterId"])
        app = FastAPI()
        app.include_router(build_publication_router(store=store))
        # When a teacher passes those lessons directly to the old publication API
        with TestClient(app) as client:
            response = client.post("/api/publications", json={
                "title": "Bypass attempt", "lessonIds": generated["currentLessonIds"],
            })
        # Then it is rejected before creating a publication, preserving chapter-only review/publish semantics
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(store.list_publications(), [])
        self.assertIsNone(service.get(chapter["chapterId"])["publicationId"])

    def test_user_publishes_when_the_database_rejects_the_final_write_then_no_partial_publication_survives(self) -> None:
        # Given a reviewed chapter and a database constraint rejecting its final publication linkage
        store = AppStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(store.close)
        service = ChapterCourseService(store)
        chapter = service.create({
            "subject": "math", "title": "Original function chapter",
            "source": {"license": "Original synthetic content", "pages": [
                {"page": 1, "text": "一次函数 y=2x+1。"},
            ]},
        })
        generated = service.generate(chapter["chapterId"])
        lesson_id = generated["currentLessonIds"][0]
        service.review(chapter["chapterId"], lesson_id, "approve", "acceptance-teacher",
                       expected_record_version=generated["recordVersion"])
        before = service.get(chapter["chapterId"])
        with store.engine.begin() as connection:
            connection.exec_driver_sql("""
                CREATE FUNCTION reject_chapter_publication_link() RETURNS trigger AS $$
                BEGIN
                    RAISE EXCEPTION 'acceptance publication linkage rejected';
                END;
                $$ LANGUAGE plpgsql
            """)
            connection.exec_driver_sql("""
                CREATE TRIGGER reject_chapter_publication_link
                BEFORE UPDATE ON chapter_records
                FOR EACH ROW WHEN (NEW.status = 'published')
                EXECUTE FUNCTION reject_chapter_publication_link()
            """)
        try:
            # When publishing fails at the final database write
            with self.assertRaises(DBAPIError):
                service.publish(chapter["chapterId"])
            # Then all publication and chapter state remains at the reviewed pre-publication snapshot
            self.assertEqual(store.list_publications(), [])
            after = service.get(chapter["chapterId"])
            self.assertEqual(after["publicationId"], before["publicationId"])
            self.assertEqual(after["publications"], before["publications"])
            self.assertEqual(after["recordVersion"], before["recordVersion"])
            self.assertEqual(after["lessons"][0]["status"], before["lessons"][0]["status"])
        finally:
            with store.engine.begin() as connection:
                connection.exec_driver_sql("DROP TRIGGER reject_chapter_publication_link ON chapter_records")
                connection.exec_driver_sql("DROP FUNCTION reject_chapter_publication_link()")

        # A successful retry creates exactly one reachable immutable publication.
        published = service.publish(chapter["chapterId"])
        self.assertEqual(len(store.list_publications()), 1)
        self.assertEqual(service.get_published(chapter["chapterId"])["publicationId"], published["publicationId"])

        # The legacy reader cannot serve chapter blocks outside the source-safe projection.
        app = FastAPI()
        app.include_router(build_publication_router(store=store))
        with TestClient(app) as client:
            self.assertEqual(client.get(f"/api/publications/{published['publicationId']}").status_code, 404)
