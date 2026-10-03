"""Acceptance of immutable, teacher-only previews of uploaded source pages."""

from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from application.services.chapter_courses import ChapterCourseService
from persistence.app_store import AppStore
from routers.chapter_routes import build_chapter_router
from tests.postgres_test_support import PostgresTestCase


class ChapterSourcePreviewTests(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = AppStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.store.close)
        self.directory = self.store.upload_root / "preview-source"
        self.directory.mkdir(parents=True)
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=300)
        writer.write(self.directory / "source.pdf")
        self.store.save_job({
            "uploadId": "preview-source", "filename": "original.pdf", "contentType": "application/pdf",
            "size": (self.directory / "source.pdf").stat().st_size, "chunkSize": 1000,
            "totalChunks": 1, "directory": self.directory, "status": "complete",
            "sourceText": "<!-- page 1 -->\nOriginal source text.", "startedAt": time.time(),
        })
        self.service = ChapterCourseService(self.store)
        app = FastAPI()
        app.include_router(build_chapter_router(self.service))
        self.client = self.enterContext(TestClient(app))

    def _create(self):
        return self.service.create({"subject": "math", "title": "Original page",
            "source": {"uploadId": "preview-source", "license": "Original test fixture"}})

    def test_user_opens_a_source_page_then_the_preview_is_pinned_to_the_original_pdf(self) -> None:
        # Given an uploaded original PDF and its completed OCR snapshot
        chapter = self._create()
        revision = chapter["sourceRevisions"][0]
        # When the teacher requests a source preview
        managed = self.client.get(f"/api/chapters/{chapter['chapterId']}").json()
        url = managed["sourceRevisions"][0]["pages"][0]["previewUrl"]
        response = self.client.get(url)
        # Then an actual raster page is served, with stable revision identity
        self.assertEqual(response.status_code, 200, response.text[:100] if response.status_code != 200 else "")
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertTrue(response.content.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn(revision["sourceRevisionId"], url)
        # A replaced upload cannot silently rewrite this old source revision.
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=200)
        writer.write(self.directory / "source.pdf")
        self.assertEqual(self.client.get(url).status_code, 409)

    def test_user_supplies_a_forged_preview_url_or_page_then_no_arbitrary_file_is_served(self) -> None:
        # Given a chapter with typed source text and no uploaded original
        chapter = self.service.create({"subject": "math", "title": "Typed source", "source": {
            "license": "Original fixture", "pages": [{"page": 1, "text": "y=2x+1.",
                "previewUrl": "https://example.invalid/private"}],
        }})
        # When retrieving the source or requesting a nonexistent page
        managed = self.client.get(f"/api/chapters/{chapter['chapterId']}").json()
        revision = managed["sourceRevisions"][0]
        # Then preview metadata is server-derived and missing originals fail closed
        self.assertIsNone(revision["pages"][0]["previewUrl"])
        path = f"/api/chapters/{chapter['chapterId']}/sources/{revision['sourceRevisionId']}/pages/1/preview"
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get(path.replace('/pages/1/', '/pages/2/')).status_code, 404)

    def test_user_opens_a_preview_with_an_escaped_asset_directory_then_no_file_is_written_outside_the_upload(self) -> None:
        # Given an original PDF whose asset directory has been replaced by a symlink
        chapter = self._create()
        outside = self.data_root / "outside-assets"
        outside.mkdir()
        (self.directory / "assets").symlink_to(outside, target_is_directory=True)
        url = self.client.get(f"/api/chapters/{chapter['chapterId']}").json()["sourceRevisions"][0]["pages"][0]["previewUrl"]
        # When the teacher requests the page
        response = self.client.get(url)
        # Then the boundary rejects it before any raster is written
        self.assertEqual(response.status_code, 409)
        self.assertEqual(list(outside.iterdir()), [])
