"""PostgreSQL acceptance scenarios for the chapter-aware review inbox."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from application import create_app
from application.services.chapter_source_preview import file_hash
from application.services.quality_review import QualityReviewService
from persistence.app_store import AppStore
from persistence.auth_store import AuthSessionStore
from persistence.lesson_media_store import LessonMediaStore
from persistence.quality_review_store import QualityReviewStore
from routers.auth_routes import COOKIE_NAME
from routers.quality_review_routes import build_quality_review_router
from tests.auth_test_support import environment
from tests.postgres_test_support import PostgresTestCase


class QualityReviewInboxPostgresAcceptanceTests(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.app_store = AppStore(database_url=self.database_url, data_root=str(self.data_root))
        self.addCleanup(self.app_store.close)
        self.review_store = QualityReviewStore(
            database_url=self.database_url,
            engine=self.app_store.engine,
            data_root=str(self.data_root),
        )
        self.addCleanup(self.review_store.close)
        self.media_store = LessonMediaStore(database_url=self.database_url, data_root=str(self.data_root))
        self.addCleanup(self.media_store.close)
        self.source_root = self.data_root / "quality-review" / "sources"
        self.source_root.mkdir(parents=True)
        self.algebra_pdf = _pdf_with_text("Verified algebra source quote.")
        self.writing_pdf = _pdf_with_text("Verified writing source quote.")
        (self.source_root / "ElementaryAlgebra2e-WEB.pdf").write_bytes(self.algebra_pdf)
        (self.source_root / "WritingGuide-WEB.pdf").write_bytes(self.writing_pdf)
        self.algebra_hash = file_hash(self.source_root / "ElementaryAlgebra2e-WEB.pdf")
        self.writing_hash = file_hash(self.source_root / "WritingGuide-WEB.pdf")

        self.app = FastAPI()
        self.app.state.auth_mode = "demo"
        self.app.include_router(build_quality_review_router(QualityReviewService(
            self.review_store,
            source_root=self.source_root,
            source_store=self.app_store,
            chapter_store=self.app_store,
            media_store=self.media_store,
        )))
        self.client = self.enterContext(TestClient(self.app))

    def test_user_sees_only_cases_bound_to_the_current_pdf_revision_and_page(self) -> None:
        """user Given chapters with current and historical source versions When opening the inbox Then only exact current PDF pages are linked."""
        case_upload_id = self._register_upload("managed-algebra-upload", "managed-origin.pdf", self.algebra_pdf)
        chapter_upload_id = self._register_upload("chapter-algebra-upload", "chapter-origin.pdf", self.algebra_pdf)
        self._add_chapter("current-match", [self._revision(
            "revision-current", chapter_upload_id, self.algebra_hash, 1, 1, [1],
        )])
        self._add_chapter("outside-range", [self._revision(
            "revision-range", chapter_upload_id, self.algebra_hash, 2, 2, [2],
        )])
        self._add_chapter("page-not-in-snapshot", [self._revision(
            "revision-page-set", chapter_upload_id, self.algebra_hash, 1, 2, [2],
        )])
        self._add_chapter("historical-only-match", [
            self._revision("revision-old", chapter_upload_id, self.algebra_hash, 1, 1, [1]),
            self._revision("revision-new", chapter_upload_id, "f" * 64, 1, 1, [1]),
        ])

        created = self.client.post("/api/quality-reviews/packages", json=self._packet(
            [self._case("case-current", 1, upload_id=case_upload_id, source_asset_id="openstax-elementary-algebra-2e")],
            source_title="Untrusted package title",
        ))
        self.assertEqual(created.status_code, 201, created.text)

        # When the teacher opens the global and chapter-filtered inbox
        global_response = self.client.get("/api/quality-reviews/inbox")
        linked_response = self.client.get(
            "/api/quality-reviews/inbox",
            params={"chapterId": "current-match"},
        )
        conflicting_filter = self.client.get(
            "/api/quality-reviews/inbox",
            params={"chapterId": "current-match", "uploadId": chapter_upload_id},
        )

        # Then the server ignores client-supplied chapter/title hints and old revisions
        self.assertEqual(global_response.status_code, 200, global_response.text)
        body = global_response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["unlinkedCount"], 0)
        item = body["items"][0]
        self.assertEqual(item["chapterIds"], ["current-match"])
        self.assertEqual(item["uploadId"], case_upload_id)
        self.assertEqual(item["sourceTitle"], "managed-origin.pdf")
        self.assertEqual(item["sourceKey"], f"sha256:{self.algebra_hash}")
        self.assertNotEqual(item["sourceTitle"], "Untrusted package title")
        self.assertEqual(linked_response.json()["total"], 1)
        self.assertEqual(conflicting_filter.json()["items"], [])

    def test_user_does_not_keep_a_missing_upload_as_the_current_material_context(self) -> None:
        """user Given an imported case whose original upload disappeared and a current same-SHA upload When opening the inbox Then only the current valid upload is associated while the old source title remains unknown."""
        original_upload = self._register_upload("removed-source-upload", "removed-origin.pdf", self.algebra_pdf)
        current_upload = self._register_upload("replacement-source-upload", "current-origin.pdf", self.algebra_pdf)
        created = self.client.post("/api/quality-reviews/packages", json=self._packet([
            self._case("stale-upload-case", 1, upload_id=original_upload),
        ]))
        self.assertEqual(created.status_code, 201, created.text)
        self._add_chapter("replacement-chapter", [self._revision(
            "replacement-revision", current_upload, self.algebra_hash, 1, 1, [1],
        )])
        (self.app_store.upload_root / original_upload / "source.pdf").unlink()

        response = self.client.get("/api/quality-reviews/inbox")

        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()["items"][0]
        self.assertEqual(item["sourceKey"], f"sha256:{self.algebra_hash}")
        self.assertEqual(item["chapterIds"], ["replacement-chapter"])
        self.assertEqual(item["uploadId"], current_upload)
        self.assertEqual(item["sourceTitle"], "未识别教材来源")

    def test_user_keeps_18_unrecognized_legacy_assets_in_the_global_unlinked_queue(self) -> None:
        """user Given 18 imported legacy cases whose asset IDs no longer resolve When opening the inbox Then every case remains visible and unlinked."""
        legacy_cases = []
        for index in range(18):
            checksum = self.algebra_hash if index < 12 else self.writing_hash
            legacy_cases.append({
                "id": f"legacy-case-{index:02d}",
                "subject": "math" if index < 12 else "english",
                "sourcePage": 1,
                "source": {
                    "sourceAssetId": f"legacy-openstax-asset-{index:02d}",
                    "contentSha256": checksum,
                    "assetVerification": "server_hash_verified",
                    "title": "Untrusted legacy title",
                    "url": "https://openstax.org/details/books/legacy",
                    "license": "CC BY 4.0",
                    "version": "legacy",
                },
                "sourceLocator": {"quote": "Historical quote"},
                "input": {"question": f"Legacy question {index}"},
                "expected": {"answer": "pending"},
                "actual": {"answer": "pending"},
            })
        self.review_store.create_package(
            title="Historical package",
            source_kind="real_open_material",
            source={"assetVerification": "server_hash_verified", "counted": False},
            cases=legacy_cases,
            actor_id="historical-import",
        )

        # When the teacher opens the unfiltered inbox
        response = self.client.get("/api/quality-reviews/inbox")

        # Then none are dropped, falsely linked, or duplicated
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["total"], 18)
        self.assertEqual(body["pendingCount"], 18)
        self.assertEqual(body["reviewedCount"], 0)
        self.assertEqual(body["unlinkedCount"], 18)
        self.assertEqual(len({item["caseId"] for item in body["items"]}), 18)
        self.assertTrue(all(item["chapterIds"] == [] for item in body["items"]))
        self.assertTrue(all(item["uploadId"] is None for item in body["items"]))
        self.assertTrue(all(item["sourceTitle"] == "未识别教材来源" for item in body["items"]))

    def test_user_sees_pending_and_reviewed_counts_independent_of_include_reviewed(self) -> None:
        """user Given linked cases with pending, needs-changes and final decisions When filtering the inbox Then counts remain stable and includeReviewed only changes items."""
        upload_id = self._register_upload("status-upload", "status-source.pdf", self.algebra_pdf)
        self._add_chapter("status-chapter", [self._revision(
            "status-revision", upload_id, self.algebra_hash, 1, 1, [1],
        )])
        packet = self._packet([
            self._case("pending-case", 1, upload_id=upload_id),
            self._case("accepted-case", 1, upload_id=upload_id),
            self._case("needs-changes-case", 1, upload_id=upload_id),
        ])
        created = self.client.post("/api/quality-reviews/packages", json=packet)
        self.assertEqual(created.status_code, 201, created.text)
        package_id = created.json()["packageId"]
        cases = self.client.get(f"/api/quality-reviews/packages/{package_id}/cases").json()["items"]
        case_ids = {item["externalCaseId"]: item["caseId"] for item in cases}
        for external_id, decision in (("accepted-case", "accepted"), ("needs-changes-case", "needs_changes")):
            reviewed = self.client.post(
                f"/api/quality-reviews/cases/{case_ids[external_id]}/decisions",
                json={
                    "expectedRecordVersion": 1,
                    "decision": decision,
                    "rationale": "已查看来源页与当前输出。",
                    "verifiedQuote": "Verified algebra source quote.",
                },
            )
            self.assertEqual(reviewed.status_code, 200, reviewed.text)

        # When the teacher filters by the linked chapter and upload
        pending_view = self.client.get(
            "/api/quality-reviews/inbox",
            params={"chapterId": "status-chapter", "uploadId": upload_id},
        )
        complete_view = self.client.get(
            "/api/quality-reviews/inbox",
            params={"chapterId": "status-chapter", "uploadId": upload_id, "includeReviewed": True},
        )

        # Then needs_changes stays pending, only final accept/reject is reviewed, and total is the item count
        self.assertEqual(pending_view.status_code, 200, pending_view.text)
        pending_body = pending_view.json()
        self.assertEqual(pending_body["total"], 2)
        self.assertEqual(pending_body["total"], len(pending_body["items"]))
        self.assertEqual(pending_body["pendingCount"], 2)
        self.assertEqual(pending_body["reviewedCount"], 1)
        self.assertEqual(pending_body["unlinkedCount"], 0)
        self.assertEqual(
            {item["reviewStatus"] for item in pending_body["items"]},
            {"pending_human_review", "needs_changes"},
        )
        complete_body = complete_view.json()
        self.assertEqual(complete_body["total"], 3)
        self.assertEqual(complete_body["total"], len(complete_body["items"]))
        self.assertEqual(complete_body["pendingCount"], 2)
        self.assertEqual(complete_body["reviewedCount"], 1)
        self.assertEqual(
            self.client.get("/api/quality-reviews/inbox", params={"chapterId": "missing"}).json()["items"],
            [],
        )

    def test_user_sees_only_latest_media_for_the_current_published_snapshot(self) -> None:
        """user Given a published course whose chapter has a newer draft source When opening filtered inbox Then only latest media for the current publication is shown under its frozen source upload."""
        published_upload = self._register_upload("published-source", "published.pdf", self.algebra_pdf)
        draft_upload = self._register_upload("new-draft-source", "new-draft.pdf", self.writing_pdf)
        self.app_store.save_lesson({
            "lessonId": "published-lesson",
            "sourceUploadId": published_upload,
            "title": "Published lesson",
            "status": "published",
            "questionPayload": {"quality": {"status": "ready"}},
        })
        self.app_store.save_lesson({
            "lessonId": "draft-only-lesson",
            "sourceUploadId": draft_upload,
            "title": "Draft-only lesson",
            "status": "draft",
        })
        self.app_store.save_lesson({
            "lessonId": "previous-publication-lesson",
            "sourceUploadId": published_upload,
            "title": "Previous publication lesson",
            "status": "published",
            "questionPayload": {"quality": {"status": "ready"}},
        })
        previous_publication = self.app_store.create_chapter_publication(
            publication_id="previous-publication",
            title="Previous publication",
            source_upload_id=published_upload,
            lesson_ids=["previous-publication-lesson"],
            status="draft",
            created_at=1,
            version=1,
            revision_of=None,
        )
        self.app_store.update_publication_status(previous_publication["publicationId"], "in_review")
        self.app_store.update_publication_status(previous_publication["publicationId"], "published")
        current_publication = self.app_store.create_chapter_publication(
            publication_id="current-publication",
            title="Current publication",
            source_upload_id=published_upload,
            lesson_ids=["published-lesson"],
            status="draft",
            created_at=2,
            version=2,
            revision_of="previous-publication",
        )
        self.app_store.update_publication_status(current_publication["publicationId"], "in_review")
        self.app_store.update_publication_status(current_publication["publicationId"], "published")
        self._add_chapter_with_publications({
            "chapterId": "published-chapter",
            "subject": "math",
            "title": "Published chapter",
            "status": "draft",
            "recordVersion": 1,
            "sourceRevisions": [
                self._revision("published-source-revision", published_upload, self.algebra_hash, 1, 1, [1]),
                self._revision("new-draft-revision", draft_upload, self.writing_hash, 1, 1, [1]),
            ],
            "lessons": [],
            "currentLessonIds": ["draft-only-lesson"],
            "publicationId": "current-publication",
            "publications": [
                {"publicationId": "previous-publication", "version": 1, "sourceRevisionId": "published-source-revision"},
                {"publicationId": "current-publication", "version": 2, "sourceRevisionId": "published-source-revision"},
            ],
            "createdAt": 1,
        })

        for revision in (
            self._media_revision("current-media-v1", "current-media-series", 1, "published-chapter", "current-publication", "published-lesson", "published-source-revision", "draft"),
            self._media_revision("current-media-v2", "current-media-series", 2, "published-chapter", "current-publication", "published-lesson", "published-source-revision", "consistency_review_required", title="Latest review title"),
            self._media_revision("rendering-media", "rendering-series", 1, "published-chapter", "current-publication", "published-lesson", "published-source-revision", "rendering"),
            self._media_revision("resolved-media-v1", "resolved-series", 1, "published-chapter", "current-publication", "published-lesson", "published-source-revision", "draft"),
            self._media_revision("resolved-media-v2", "resolved-series", 2, "published-chapter", "current-publication", "published-lesson", "published-source-revision", "approved"),
            self._media_revision("illegal-lesson-media", "illegal-lesson-series", 1, "published-chapter", "current-publication", "draft-only-lesson", "new-draft-revision", "draft"),
            self._media_revision("old-publication-media", "old-publication-series", 1, "published-chapter", "previous-publication", "previous-publication-lesson", "published-source-revision", "draft"),
        ):
            self.media_store.create_revision(revision)

        chapter_context = self.client.get("/api/quality-reviews/inbox", params={"chapterId": "published-chapter"})
        frozen_upload_context = self.client.get(
            "/api/quality-reviews/inbox",
            params={"chapterId": "published-chapter", "uploadId": published_upload},
        )
        draft_upload_context = self.client.get(
            "/api/quality-reviews/inbox",
            params={"chapterId": "published-chapter", "uploadId": draft_upload},
        )

        self.assertEqual(chapter_context.status_code, 200, chapter_context.text)
        self.assertEqual(chapter_context.json()["mediaPendingCount"], 1)
        self.assertEqual(
            {item["mediaId"] for item in chapter_context.json()["mediaTasks"]},
            {"current-media-v2", "rendering-media"},
        )
        by_id = {item["mediaId"]: item for item in chapter_context.json()["mediaTasks"]}
        self.assertEqual(by_id["current-media-v2"]["title"], "Latest review title")
        self.assertEqual(by_id["current-media-v2"]["uploadId"], published_upload)
        self.assertEqual(by_id["current-media-v2"]["sourceRevisionId"], "published-source-revision")
        self.assertEqual(by_id["current-media-v2"]["sourceKey"], f"sha256:{self.algebra_hash}")
        self.assertEqual(frozen_upload_context.json()["mediaPendingCount"], 1)
        self.assertEqual(len(frozen_upload_context.json()["mediaTasks"]), 2)
        self.assertEqual(draft_upload_context.json()["mediaTasks"], [])
        self.assertEqual(draft_upload_context.json()["mediaPendingCount"], 0)

    def test_user_student_cannot_read_the_teacher_review_inbox(self) -> None:
        """user Given a protected app with teacher and student sessions When each opens the review inbox Then only the teacher can read it."""
        with environment({
            "AUTH_MODE": "protected",
            "AUTH_COOKIE_SECURE": "1",
            "TEACHER_BOOTSTRAP_SECRET": "quality-inbox-acceptance-secret-2026",
            "CORS_ORIGINS": "https://testserver",
        }):
            protected_app = create_app()
        auth_store = AuthSessionStore(engine=self.app_store.engine)
        protected_app.state.auth_session_store = auth_store
        protected_app.include_router(build_quality_review_router(QualityReviewService(
            self.review_store,
            source_root=self.source_root,
            source_store=self.app_store,
            chapter_store=self.app_store,
            media_store=self.media_store,
        )))
        teacher_token, _ = auth_store.create_session(role="teacher", learner_id=None)
        student_token, _ = auth_store.create_session(role="student", learner_id="inbox-student")
        anonymous_client = self.enterContext(TestClient(protected_app, base_url="https://testserver"))
        teacher_client = self.enterContext(TestClient(protected_app, base_url="https://testserver"))
        student_client = self.enterContext(TestClient(protected_app, base_url="https://testserver"))
        teacher_client.cookies.set(COOKIE_NAME, teacher_token)
        student_client.cookies.set(COOKIE_NAME, student_token)

        anonymous = anonymous_client.get("/api/quality-reviews/inbox")
        teacher = teacher_client.get("/api/quality-reviews/inbox")
        student = student_client.get("/api/quality-reviews/inbox")

        self.assertEqual(anonymous.status_code, 401, anonymous.text)
        self.assertEqual(teacher.status_code, 200, teacher.text)
        self.assertEqual(student.status_code, 403, student.text)

    def _packet(self, cases: list[dict], *, source_title: str = "Package source") -> dict:
        return {
            "title": "Review package",
            "source": {"sourceTitle": source_title},
            "cases": cases,
        }

    def _case(
        self,
        case_id: str,
        page: int,
        *,
        upload_id: str | None = None,
        source_asset_id: str | None = None,
    ) -> dict:
        checksum = self.algebra_hash if source_asset_id == "openstax-elementary-algebra-2e" or not source_asset_id else self.writing_hash
        source = {
            "url": "https://openstax.org/details/books/elementary-algebra-2e",
            "license": "CC BY 4.0",
            "version": "2e",
            "contentSha256": checksum,
            "sourceTitle": "Client spoofed source title",
            "chapterId": "client-spoofed-chapter",
        }
        if upload_id:
            source["uploadId"] = upload_id
        if source_asset_id:
            source["sourceAssetId"] = source_asset_id
        quote = "Verified algebra source quote." if checksum == self.algebra_hash else "Verified writing source quote."
        return {
            "id": case_id,
            "subject": "math" if checksum == self.algebra_hash else "english",
            "sourcePage": page,
            "source": source,
            "sourceLocator": {"quote": quote},
            "input": {"question": f"Question {case_id}"},
            "expected": {"answer": "expected"},
            "actual": {"answer": "observed"},
        }

    def _register_upload(self, upload_id: str, filename: str, pdf_bytes: bytes) -> str:
        directory = self.app_store.upload_root / upload_id
        directory.mkdir(parents=True)
        source_path = directory / "source.pdf"
        source_path.write_bytes(pdf_bytes)
        text = "<!-- page 1 -->\nVerified algebra source quote."
        self.app_store.save_job({
            "uploadId": upload_id,
            "importId": f"test-{upload_id}",
            "filename": filename,
            "contentType": "application/pdf",
            "size": len(pdf_bytes),
            "chunkSize": len(pdf_bytes),
            "totalChunks": 1,
            "directory": directory,
            "status": "complete",
            "progress": 100,
            "message": "ready",
            "startedAt": 1,
            "updatedAt": 1,
            "completedAt": 1,
            "sourceText": text,
        })
        return upload_id

    def _add_chapter(self, chapter_id: str, revisions: list[dict]) -> None:
        self.app_store.create_chapter({
            "chapterId": chapter_id,
            "subject": "math",
            "title": "Chapter title is not a source identity",
            "status": "draft",
            "recordVersion": 1,
            "sourceRevisions": revisions,
            "lessons": [],
            "currentLessonIds": [],
            "publications": [],
            "createdAt": 1,
        })

    def _add_chapter_with_publications(self, chapter: dict) -> None:
        self.app_store.create_chapter(chapter)

    @staticmethod
    def _media_revision(
        media_id: str,
        series_id: str,
        version: int,
        chapter_id: str,
        publication_id: str,
        lesson_id: str,
        source_revision_id: str,
        status: str,
        *,
        title: str | None = None,
    ) -> dict:
        return {
            "mediaId": media_id,
            "seriesId": series_id,
            "version": version,
            "chapterId": chapter_id,
            "publicationId": publication_id,
            "lessonId": lesson_id,
            "sourceRevisionId": source_revision_id,
            "sourceFingerprint": "a" * 64,
            "title": title or media_id,
            "subject": "math",
            "license": "CC BY 4.0",
            "sourceSnapshot": {},
            "scenes": [],
            "rendererSnapshot": {},
            "status": status,
            "distributionScope": "student",
            "createdBy": "test-teacher",
            "createdAt": float(version),
        }

    @staticmethod
    def _revision(
        revision_id: str,
        upload_id: str,
        source_hash: str,
        page_start: int,
        page_end: int,
        pages: list[int],
    ) -> dict:
        return {
            "sourceRevisionId": revision_id,
            "uploadId": upload_id,
            "sourceFileSha256": source_hash,
            "sourceTitle": "Untrusted revision title",
            "pageStart": page_start,
            "pageEnd": page_end,
            "pages": [{"page": number} for number in pages],
        }


def _pdf_with_text(text: str) -> bytes:
    safe_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({safe_text}) Tj ET".encode("ascii")
    bodies = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    document = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(bodies, start=1):
        offsets.append(len(document))
        document.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_offset = len(document)
    document.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    document.extend(b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets[1:]))
    document.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode()
    )
    return bytes(document)
