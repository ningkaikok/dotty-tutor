"""Business acceptance for reversible legacy textbook withdrawal on PostgreSQL."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any

from sqlalchemy import event, insert, select, text, update

from application.services.legacy_textbook_cleanup import (
    LegacyTextbookCleanupError,
    LegacyTextbookCleanupService,
    manifest_sha256,
)
from persistence.legacy_cleanup_guards import lock_upload_for_legacy_write
from persistence.legacy_textbook_cleanup_store import (
    LegacyTextbookCleanupConflict,
    LegacyTextbookCleanupStore,
    legacy_textbook_cleanup_manifests,
)
from persistence.quality_review_store import (
    quality_review_case_revisions,
    quality_review_cases,
    quality_review_decisions,
    quality_review_packages,
)
from persistence.schema import (
    assignments,
    background_jobs,
    batch_questions,
    chapter_records,
    exercise_attempts,
    knowledge_points,
    learning_classes,
    learning_sessions,
    lesson_documents,
    lesson_publications,
    mastery_states,
    question_revisions,
    run_snapshots,
    upload_jobs,
)
from persistence.search_store import lesson_search_documents
from tests.postgres_test_support import PostgresTestCase

UPLOAD_ID = "english-upload-fixture"
SOURCE_BYTES = b"source-pdf-fixture-preserved"
SOURCE_SHA256 = hashlib.sha256(SOURCE_BYTES).hexdigest()
TEXTBOOK_SENTINEL = "PRIVATE SOURCE TEXT MUST NEVER ENTER THE MANIFEST"


class LegacyTextbookCleanupPostgresAcceptance(PostgresTestCase):
    """Exercise the exact manifest and CAS lifecycle against disposable PostgreSQL."""

    def setUp(self) -> None:
        super().setUp()
        self.upload_root = self.data_root / "uploads" / UPLOAD_ID
        self.upload_root.mkdir(parents=True)
        (self.upload_root / "source.pdf").write_bytes(SOURCE_BYTES)
        ocr_page = self.upload_root / "assets" / "chapter-page-023" / "source.md"
        ocr_page.parent.mkdir(parents=True)
        ocr_page.write_text(TEXTBOOK_SENTINEL, encoding="utf-8")
        self.store = LegacyTextbookCleanupStore(self.engine, data_root=self.data_root)
        self.service = LegacyTextbookCleanupService(self.store)
        self._seed_english_textbook()

    def _seed_english_textbook(self) -> None:
        now = 100.0
        with self.engine.begin() as connection:
            connection.execute(insert(upload_jobs).values(
                upload_id=UPLOAD_ID,
                import_id=f"pdf-{SOURCE_SHA256[:12]}",
                filename="English Textbook.pdf",
                content_type="application/pdf",
                size=len(SOURCE_BYTES),
                chunk_size=1024,
                total_chunks=1,
                source_text=TEXTBOOK_SENTINEL,
                directory=str(self.upload_root),
                status="completed",
                progress=100,
                message="completed",
                result_json={
                    "materialKind": "textbook",
                    "sourcePageCount": 773,
                    "firstBatchJson": {
                        "batchId": "legacy-result-batch",
                        "questions": [{
                            "questionId": "legacy-result-question",
                            "lessonId": "english-lesson-01-01",
                            "prompt": "LEGACY GENERATED QUESTION BODY",
                        }],
                    },
                },
                started_at=now,
                updated_at=now,
                completed_at=now,
            ))

            lesson_ids: list[str] = []
            chapters: list[dict[str, Any]] = []
            for chapter_index in range(10):
                chapter_id = f"english-chapter-{chapter_index + 1:02d}"
                teaching_mode = None if chapter_index < 5 else "tutorial"
                chapter_lessons = [
                    f"english-lesson-{chapter_index + 1:02d}-{lesson_index + 1:02d}"
                    for lesson_index in range(4 if teaching_mode is None else 1)
                ]
                lesson_ids.extend(chapter_lessons)
                review_issues = (
                    [
                        {"lessonId": lesson_id, "code": "draft_requires_review"}
                        for lesson_id in chapter_lessons
                    ]
                    if teaching_mode is None
                    else []
                )
                revision_id = f"english-source-revision-{chapter_index + 1:02d}"
                revision = {
                    "sourceRevisionId": revision_id,
                    "uploadId": UPLOAD_ID,
                    "sourceFileSha256": SOURCE_SHA256,
                    "pageStart": 23 + chapter_index * 30,
                    "pageEnd": 52 + chapter_index * 30,
                }
                chapter = {
                    "chapterId": chapter_id,
                    "subject": "english",
                    "title": f"Chapter {chapter_index + 1}",
                    "status": "in_review",
                    "recordVersion": 2,
                    "teachingMode": teaching_mode,
                    "sourceRevisions": [revision],
                    "lessons": [
                        {
                            "lessonId": lesson_id,
                            "sourceRevisionId": revision_id,
                            "status": "in_review",
                            "reviews": [],
                            "reviewIssues": [],
                        }
                        for lesson_id in chapter_lessons
                    ],
                    "currentLessonIds": chapter_lessons,
                    "reviewIssues": review_issues,
                }
                chapters.append(chapter)
                connection.execute(insert(chapter_records).values(
                    chapter_id=chapter_id,
                    subject="english",
                    status="in_review",
                    chapter_json=chapter,
                    record_version=2,
                    created_at=now,
                    updated_at=now,
                ))

            # One extra generated lesson came from the incorrectly routed quiz batch.
            lesson_ids.append("english-batch-generated-lesson")
            for lesson_id in lesson_ids:
                connection.execute(insert(lesson_documents).values(
                    lesson_id=lesson_id,
                    source_upload_id=UPLOAD_ID,
                    title=lesson_id,
                    version=1,
                    status="in_review",
                    knowledge_points_json=[],
                    blocks_json=[{"text": TEXTBOOK_SENTINEL}],
                    question_json={"kind": "legacy-generated"},
                    guide_cards_json=[],
                    created_at=now,
                    updated_at=now,
                ))

            connection.execute(insert(question_revisions).values(
                revision_id="english-generated-question-revision",
                upload_id=UPLOAD_ID,
                source_question_key="legacy-question-1",
                revision_number=1,
                operation="generate",
                previous_revision_id=None,
                payload_json={"kind": "generated"},
                guide_cards_json=[],
                run_id="fixture-run",
                created_at=now,
                revision_source="model_generated",
            ))
            connection.execute(insert(batch_questions).values(
                upload_id=UPLOAD_ID,
                batch_id="batch-001-q-index-001",
                question_id="legacy-question-1",
                payload_json={"kind": "generated"},
                guide_cards_json=[],
                created_at=now,
                current_revision_id="english-generated-question-revision",
            ))

            for job_id, status, lease_owner in (
                ("legacy-job-queued", "queued", None),
                ("legacy-job-running", "running", "worker-fixture"),
                ("legacy-job-finished", "succeeded", None),
            ):
                connection.execute(insert(background_jobs).values(
                    job_id=job_id,
                    job_type="chapter.lesson.generate",
                    idempotency_key=f"cleanup-fixture-{job_id}",
                    payload_json={
                        "uploadId": UPLOAD_ID,
                        "chapterId": chapters[0]["chapterId"],
                    },
                    status=status,
                    max_attempts=3,
                    attempt_count=1 if status != "queued" else 0,
                    cancel_requested=False,
                    lease_owner=lease_owner,
                    lease_expires_at=now + 30 if lease_owner else None,
                    progress=0,
                    message="fixture job",
                    created_at=now,
                    updated_at=now,
                    started_at=now if lease_owner else None,
                ))

    def _plan(self) -> dict[str, Any]:
        return self.service.plan([UPLOAD_ID], now=1000.0)

    def test_user_can_dry_run_exact_english_target_without_mutating_legacy_or_source_data(self) -> None:
        # Given ten obsolete chapter records, their 26 lesson views, a legacy batch pointer, and original source assets
        before = (self.upload_root / "source.pdf").read_bytes()
        before_ocr = (self.upload_root / "assets/chapter-page-023/source.md").read_bytes()

        # When the user creates the default dry-run manifest for the verified English upload
        planned = self._plan()
        manifest = planned["manifest"]
        target = manifest["targets"][0]

        # Then the manifest is exact, hashes are bound to this database, and it contains no source body
        self.assertEqual(target["upload"]["uploadId"], UPLOAD_ID)
        self.assertEqual(target["upload"]["materialKind"], "textbook")
        self.assertEqual(len(target["chapters"]), 10)
        self.assertEqual(sum(len(chapter["lessonIds"]) for chapter in target["chapters"]), 25)
        self.assertEqual(len(target["lessons"]), 26)
        self.assertEqual(len(target["batchQuestions"]), 1)
        self.assertEqual(len(target["lessonIds"]), 26)
        self.assertEqual(target["batchIds"], ["batch-001-q-index-001", "legacy-result-batch"])
        self.assertIn("legacy-result-question", target["questionIds"])
        self.assertEqual(target["protectedRefs"]["questionRevisions"], 1)
        serialized = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(TEXTBOOK_SENTINEL, serialized)
        self.assertNotIn("LEGACY GENERATED QUESTION BODY", serialized)
        self.assertEqual(manifest_sha256(manifest), planned["manifestSha256"])
        self.assertTrue(planned["targetEnvironmentFingerprint"])
        self.assertEqual((self.upload_root / "source.pdf").read_bytes(), before)
        self.assertEqual((self.upload_root / "assets/chapter-page-023/source.md").read_bytes(), before_ocr)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(upload_jobs.c.status).where(
                upload_jobs.c.upload_id == UPLOAD_ID
            )).scalar_one(), "completed")
            self.assertEqual(connection.execute(select(chapter_records.c.record_version).order_by(chapter_records.c.chapter_id)).scalars().all(), [2] * 10)

    def test_user_given_an_existing_manifest_when_rewriting_it_then_identical_content_is_allowed_and_changes_are_rejected(self) -> None:
        # Given a reviewed metadata manifest written to an owned output directory
        manifest = self._plan()["manifest"]
        path = self.data_root / "reviewed-manifest.json"
        digest = self.service.write_manifest(manifest, path)
        original = path.read_bytes()
        # When saving the same artifact again or trying to replace its identity
        self.assertEqual(self.service.write_manifest(manifest, path), digest)
        with self.assertRaisesRegex(LegacyTextbookCleanupError, "拒绝覆盖"):
            self.service.write_manifest({**manifest, "manifestId": "different-reviewed-manifest"}, path)
        # Then no platform may overwrite the first reviewed artifact or leave temporary metadata
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(path.parent.glob(f".{path.name}.*")), [])
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_user_can_apply_idempotently_and_restore_without_reopening_cancelled_workers(self) -> None:
        # Given a fresh exact manifest whose upload, chapters, job states, and protected rows are backed up
        planned = self._plan()
        backup_path = Path(tempfile.mkdtemp()) / "legacy-cleanup-backup.json"
        source_before = (self.upload_root / "source.pdf").read_bytes()
        ocr_before = (self.upload_root / "assets/chapter-page-023/source.md").read_bytes()
        with self.engine.connect() as connection:
            result_before = connection.execute(select(upload_jobs.c.result_json).where(
                upload_jobs.c.upload_id == UPLOAD_ID
            )).scalar_one()

        # When a separately confirmed apply is repeated, then the same manifest remains idempotent
        first = self.service.apply(
            planned["manifest"],
            expected_manifest_sha256=planned["manifestSha256"],
            expected_target_environment_fingerprint=planned["targetEnvironmentFingerprint"],
            backup_path=backup_path,
            now=2000.0,
        )
        with self.engine.begin() as connection:
            connection.execute(insert(chapter_records).values(
                chapter_id="english-source-only-new",
                subject="english",
                status="draft",
                chapter_json={
                    "chapterId": "english-source-only-new",
                    "sourcePreparationJobId": "source-only-prep-job",
                    "subject": "english",
                    "status": "draft",
                    "recordVersion": 1,
                    "sourceRevisions": [{
                        "sourceRevisionId": "english-source-revision-new",
                        "uploadId": UPLOAD_ID,
                        "sourceFileSha256": SOURCE_SHA256,
                        "pageStart": 700,
                        "pageEnd": 701,
                    }],
                    "lessons": [],
                    "currentLessonIds": [],
                },
                record_version=1,
                created_at=2000.5,
                updated_at=2000.5,
            ))
        repeated = self.service.apply(
            planned["manifest"],
            expected_manifest_sha256=planned["manifestSha256"],
            expected_target_environment_fingerprint=planned["targetEnvironmentFingerprint"],
            backup_path=backup_path,
            now=2001.0,
        )
        self.assertEqual(first["status"], "deleted")
        self.assertEqual(len(first["chapterIds"]), 10)
        self.assertEqual(len(first["lessonIds"]), 26)
        self.assertEqual(len(first["batchIds"]), 2)
        self.assertEqual(len(first["jobIds"]), 3)
        self.assertEqual(repeated["status"], "deleted")
        self.assertTrue(repeated["idempotent"])
        # POSIX mode bits encode privacy; Windows uses inherited directory ACLs.
        if os.name != "nt":
            self.assertEqual(backup_path.stat().st_mode & 0o777, 0o600)

        # Then cleanup leaves the source upload intact, tombstones only chapters, and cancels executable jobs
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(upload_jobs.c.status).where(
                upload_jobs.c.upload_id == UPLOAD_ID
            )).scalar_one(), "completed")
            self.assertEqual(connection.execute(select(upload_jobs.c.source_text).where(
                upload_jobs.c.upload_id == UPLOAD_ID
            )).scalar_one(), TEXTBOOK_SENTINEL)
            rows = connection.execute(select(
                chapter_records.c.chapter_id, chapter_records.c.record_version, chapter_records.c.chapter_json
            ).order_by(chapter_records.c.chapter_id)).all()
            tombstoned = [row for row in rows if row.chapter_id in first["chapterIds"]]
            self.assertEqual(len(tombstoned), 10)
            self.assertTrue(all(row.record_version == 3 and row.chapter_json.get("deletedAt") for row in tombstoned))
            source_only = next(row for row in rows if row.chapter_json["chapterId"] == "english-source-only-new")
            self.assertEqual(source_only.record_version, 1)
            self.assertNotIn("deletedAt", source_only.chapter_json)
            statuses = dict(connection.execute(select(
                background_jobs.c.job_id, background_jobs.c.status
            )).all())
            self.assertEqual(statuses["legacy-job-queued"], "cancelled")
            self.assertEqual(statuses["legacy-job-running"], "running")
            self.assertEqual(statuses["legacy-job-finished"], "succeeded")
            self.assertTrue(connection.execute(select(background_jobs.c.cancel_requested).where(
                background_jobs.c.job_id == "legacy-job-running"
            )).scalar_one())
            self.assertEqual(connection.execute(select(question_revisions.c.revision_id)).scalars().all(), [
                "english-generated-question-revision"
            ])
            self.assertEqual(connection.execute(select(batch_questions.c.current_revision_id)).scalar_one(),
                             "english-generated-question-revision")
            self.assertEqual(connection.execute(select(lesson_documents.c.lesson_id)).rowcount, 26)
            self.assertEqual(connection.execute(select(upload_jobs.c.result_json).where(
                upload_jobs.c.upload_id == UPLOAD_ID
            )).scalar_one(), result_before)
            marker = connection.execute(select(legacy_textbook_cleanup_manifests).where(
                legacy_textbook_cleanup_manifests.c.upload_id == UPLOAD_ID
            )).mappings().one()
            self.assertEqual(marker["status"], "applied")
            self.assertEqual(marker["snapshot_json"]["lessonIds"], first["lessonIds"])
            self.assertIn("legacy-result-question", marker["snapshot_json"]["questionIds"])
            self.assertNotIn("english-source-only-new", marker["snapshot_json"]["chapterIds"])
            self.assertNotIn("english-source-only-new", self.store.inspect_upload(UPLOAD_ID)["chapterIds"])

        # When the user restores the exact manifest, then tombstones clear without reviving cancelled work
        restored = self.service.restore(
            backup_path,
            expected_manifest_sha256=planned["manifestSha256"],
            expected_target_environment_fingerprint=planned["targetEnvironmentFingerprint"],
            now=3000.0,
        )
        repeated_restore = self.service.restore(
            backup_path,
            expected_manifest_sha256=planned["manifestSha256"],
            expected_target_environment_fingerprint=planned["targetEnvironmentFingerprint"],
            now=3001.0,
        )
        self.assertEqual(restored["status"], "restored")
        self.assertEqual(repeated_restore["status"], "restored")
        self.assertTrue(repeated_restore["idempotent"])
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(upload_jobs.c.status).where(
                upload_jobs.c.upload_id == UPLOAD_ID
            )).scalar_one(), "completed")
            rows = connection.execute(select(
                chapter_records.c.record_version, chapter_records.c.chapter_json
            ).order_by(chapter_records.c.chapter_id)).all()
            restored_old = [row for row in rows if row.chapter_json["chapterId"] != "english-source-only-new"]
            self.assertTrue(all(row.record_version == 4 and "deletedAt" not in row.chapter_json for row in restored_old))
            source_only = next(row for row in rows if row.chapter_json["chapterId"] == "english-source-only-new")
            self.assertEqual(source_only.record_version, 1)
            self.assertNotIn("deletedAt", source_only.chapter_json)
            self.assertEqual(
                sorted(revision["sourceRevisionId"] for row in restored_old for revision in row.chapter_json["sourceRevisions"]),
                sorted(chapter["sourceRevisionIds"][0] for chapter in planned["manifest"]["targets"][0]["chapters"]),
            )
            self.assertEqual(connection.execute(select(background_jobs.c.status).where(
                background_jobs.c.job_id == "legacy-job-queued"
            )).scalar_one(), "cancelled")
            self.assertTrue(connection.execute(select(background_jobs.c.cancel_requested).where(
                background_jobs.c.job_id == "legacy-job-running"
            )).scalar_one())
            self.assertEqual(connection.execute(select(legacy_textbook_cleanup_manifests.c.status).where(
                legacy_textbook_cleanup_manifests.c.upload_id == UPLOAD_ID
            )).scalar_one(), "restored")
        self.assertEqual((self.upload_root / "source.pdf").read_bytes(), source_before)
        self.assertEqual((self.upload_root / "assets/chapter-page-023/source.md").read_bytes(), ocr_before)

    def test_user_cannot_apply_when_manifest_hash_environment_or_source_fingerprint_changed(self) -> None:
        # Given a manifest bound to the original data state and isolated database
        planned = self._plan()
        backup_path = Path(tempfile.mkdtemp()) / "must-not-exist.json"

        # When a manifest hash or environment confirmation is altered, then apply fails before creating a backup
        with self.assertRaises(LegacyTextbookCleanupError):
            self.service.apply(
                planned["manifest"],
                expected_manifest_sha256="0" * 64,
                expected_target_environment_fingerprint=planned["targetEnvironmentFingerprint"],
                backup_path=backup_path,
                now=2000.0,
            )
        with self.assertRaises(LegacyTextbookCleanupError):
            self.service.apply(
                planned["manifest"],
                expected_manifest_sha256=planned["manifestSha256"],
                expected_target_environment_fingerprint="1" * 64,
                backup_path=backup_path,
                now=2000.0,
            )
        self.assertFalse(backup_path.exists())

        # When a linked chapter changes after review, then the stale exact manifest is rejected without mutation
        with self.engine.begin() as connection:
            connection.execute(update(chapter_records).where(
                chapter_records.c.chapter_id == "english-chapter-01"
            ).values(record_version=3, updated_at=101.0))
        with self.assertRaises(LegacyTextbookCleanupError):
            self.service.apply(
                planned["manifest"],
                expected_manifest_sha256=planned["manifestSha256"],
                expected_target_environment_fingerprint=planned["targetEnvironmentFingerprint"],
                backup_path=backup_path,
                now=2000.0,
            )
        self.assertFalse(backup_path.exists())
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(upload_jobs.c.status).where(
                upload_jobs.c.upload_id == UPLOAD_ID
            )).scalar_one(), "completed")

    def test_user_cannot_apply_if_a_protected_publication_appears_after_dry_run(self) -> None:
        # Given an eligible exact dry-run manifest with no publication references
        planned = self._plan()
        target = planned["manifest"]["targets"][0]
        self.assertTrue(target["eligibleToApply"])
        backup_path = Path(tempfile.mkdtemp()) / "stale-protected-ref.json"

        # When a publication referencing this upload is created after planning, then apply re-inspects and fails closed
        with self.engine.begin() as connection:
            connection.execute(insert(lesson_publications).values(
                publication_id="publication-added-after-dry-run",
                title="Newly published legacy lesson",
                source_upload_id=UPLOAD_ID,
                lesson_ids_json=["english-lesson-01-01"],
                status="draft",
                version=1,
                created_at=1500.0,
                updated_at=1500.0,
            ))
        with self.assertRaises(LegacyTextbookCleanupError):
            self.service.apply(
                planned["manifest"],
                expected_manifest_sha256=planned["manifestSha256"],
                expected_target_environment_fingerprint=planned["targetEnvironmentFingerprint"],
                backup_path=backup_path,
                now=2000.0,
            )

        self.assertFalse(backup_path.exists())
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(
                lesson_publications.c.publication_id
            )).scalars().all(), ["publication-added-after-dry-run"])
            self.assertEqual(connection.execute(select(
                chapter_records.c.record_version
            ).where(chapter_records.c.chapter_id == "english-chapter-01")).scalar_one(), 2)

    def test_user_serializes_cleanup_after_a_protected_writer_holds_the_source_lock(self) -> None:
        # Given a writer that locks the source before adding a publication, then cleanup must wait and re-inspect
        planned = self._plan()
        backup_path = Path(tempfile.mkdtemp()) / "concurrent-writer-backup.json"
        writer = self.engine.connect()
        writer_transaction = writer.begin()
        _, marker = lock_upload_for_legacy_write(writer, UPLOAD_ID)
        self.assertIsNone(marker)
        writer.execute(insert(lesson_publications).values(
            publication_id="publication-from-locked-writer",
            title="Concurrent protected publication",
            source_upload_id=UPLOAD_ID,
            lesson_ids_json=["english-lesson-01-01"],
            status="draft",
            version=1,
            created_at=1600.0,
            updated_at=1600.0,
        ))

        cleanup_lock_requested = Event()
        cleanup_backend_pids: list[int] = []
        def observe_cleanup_lock(
            connection: Any,
            _cursor: Any,
            statement: str,
            _parameters: Any,
            _context: Any,
            _executemany: bool,
        ) -> None:
            if "FOR UPDATE" in statement.upper() and "upload_jobs" in statement:
                cleanup_backend_pids.append(connection.connection.driver_connection.info.backend_pid)
                cleanup_lock_requested.set()

        event.listen(self.engine, "before_cursor_execute", observe_cleanup_lock)
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                cleanup = executor.submit(
                    self.service.apply,
                    planned["manifest"],
                    expected_manifest_sha256=planned["manifestSha256"],
                    expected_target_environment_fingerprint=planned["targetEnvironmentFingerprint"],
                    backup_path=backup_path,
                    now=2000.0,
                )
                self.assertTrue(cleanup_lock_requested.wait(timeout=5))
                # Observe PostgreSQL reporting this exact cleanup connection blocked by the writer lock.
                deadline = monotonic() + 5
                blockers: list[int] = []
                while not blockers and monotonic() < deadline:
                    with self.engine.connect() as monitor:
                        blockers = monitor.execute(text("SELECT pg_blocking_pids(:pid)"), {
                            "pid": cleanup_backend_pids[0],
                        }).scalar_one()
                    if not blockers:
                        Event().wait(0.01)
                self.assertTrue(blockers, "cleanup never waited for the writer's shared upload lock")
                writer_transaction.commit()
                with self.assertRaises(LegacyTextbookCleanupConflict):
                    cleanup.result(timeout=5)
        finally:
            event.remove(self.engine, "before_cursor_execute", observe_cleanup_lock)
            if writer_transaction.is_active:
                writer_transaction.rollback()
            writer.close()

        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(
                lesson_publications.c.publication_id
            )).scalars().all(), ["publication-from-locked-writer"])
            self.assertEqual(connection.execute(select(
                legacy_textbook_cleanup_manifests.c.manifest_id
            )).scalars().all(), [])
            self.assertEqual(connection.execute(select(
                chapter_records.c.record_version
            ).where(chapter_records.c.chapter_id == "english-chapter-01")).scalar_one(), 2)

    def test_user_cli_defaults_to_dry_run_and_requires_apply_confirmations(self) -> None:
        # Given an isolated fixture database and its private source assets
        environment = os.environ.copy()
        environment["DATABASE_URL"] = self.database_url
        environment["DOTTY_DATA_DIR"] = str(self.data_root)
        script = Path(__file__).resolve().parents[3] / "scripts" / "cleanup_legacy_textbook_content.py"

        # When the user omits the command, then the CLI emits a read-only dry-run manifest
        default_run = subprocess.run(
            [sys.executable, str(script), "--upload-id", UPLOAD_ID, "--now", "2000"],
            capture_output=True,
            check=False,
            env=environment,
            text=True,
        )
        self.assertEqual(default_run.returncode, 0, default_run.stderr)
        output = json.loads(default_run.stdout)
        self.assertEqual(output["status"], "dry_run")
        self.assertEqual(len(output["manifest"]["targets"][0]["chapterIds"]), 10)
        self.assertNotIn(TEXTBOOK_SENTINEL, default_run.stdout)

        missing_target = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            check=False,
            env=environment,
            text=True,
        )
        self.assertEqual(missing_target.returncode, 2)
        self.assertIn("dry-run 必须提供 --upload-id", missing_target.stderr)

        # When apply omits reviewed manifest, backup, or fingerprints, then CLI rejects before any database mutation
        invalid_apply = subprocess.run(
            [sys.executable, str(script), "apply"],
            capture_output=True,
            check=False,
            env=environment,
            text=True,
        )
        self.assertEqual(invalid_apply.returncode, 2)
        self.assertIn("apply 必须提供 --manifest 和 --backup", invalid_apply.stderr)

        invalid_restore = subprocess.run(
            [sys.executable, str(script), "restore"],
            capture_output=True,
            check=False,
            env=environment,
            text=True,
        )
        self.assertEqual(invalid_restore.returncode, 2)
        self.assertIn("restore 必须提供 --backup", invalid_restore.stderr)

    def test_user_cannot_apply_unknown_paper_or_human_reviewed_material(self) -> None:
        # Given a target that is not explicitly identified as an unreviewed textbook
        for material_kind in ("paper", "unknown"):
            with self.engine.begin() as connection:
                connection.execute(update(upload_jobs).where(
                    upload_jobs.c.upload_id == UPLOAD_ID
                ).values(result_json={"materialKind": material_kind, "sourcePageCount": 773}))
            with self.assertRaises(LegacyTextbookCleanupError):
                self._plan()
            with self.engine.begin() as connection:
                connection.execute(update(upload_jobs).where(
                    upload_jobs.c.upload_id == UPLOAD_ID
                ).values(result_json={"materialKind": "textbook", "sourcePageCount": 773}))

        # Given teacher edits or review decisions on a chapter, then the target is protected
        with self.engine.begin() as connection:
            chapter = connection.execute(select(chapter_records.c.chapter_json).where(
                chapter_records.c.chapter_id == "english-chapter-01"
            )).scalar_one()
            chapter["lessons"][0]["reviews"] = [{"reviewer": "teacher-fixture", "decision": "approve"}]
            connection.execute(update(chapter_records).where(
                chapter_records.c.chapter_id == "english-chapter-01"
            ).values(chapter_json=chapter, record_version=3))
        planned = self._plan()
        self.assertFalse(planned["manifest"]["targets"][0]["eligibleToApply"])
        with self.assertRaises(LegacyTextbookCleanupError):
            self.service.apply(
                planned["manifest"],
                expected_manifest_sha256=planned["manifestSha256"],
                expected_target_environment_fingerprint=planned["targetEnvironmentFingerprint"],
                backup_path=Path(tempfile.mkdtemp()) / "blocked.json",
                now=2000.0,
            )

    def test_user_cannot_withdraw_a_publication_with_assignment_or_student_evidence(self) -> None:
        # Given a legacy publication, assignment, learning session, and exercise attempt tied to the upload
        now = 500.0
        with self.engine.begin() as connection:
            connection.execute(insert(lesson_publications).values(
                publication_id="legacy-publication",
                title="Legacy English",
                source_upload_id=UPLOAD_ID,
                lesson_ids_json=["english-lesson-01-01"],
                status="published",
                version=1,
                created_at=now,
                updated_at=now,
            ))
            connection.execute(insert(learning_classes).values(
                class_id="class-1", name="Class 1", subject="英语", grade_band="初中",
                created_at=now, updated_at=now,
            ))
            connection.execute(insert(assignments).values(
                assignment_id="assignment-1", class_id="class-1", publication_id="legacy-publication",
                title="Legacy assignment", status="active", created_at=now, updated_at=now,
            ))
            connection.execute(insert(learning_sessions).values(
                session_id="session-1", learner_id="learner-fixture", publication_id="legacy-publication",
                assignment_id="assignment-1", started_at=now, updated_at=now,
            ))
            connection.execute(insert(exercise_attempts).values(
                attempt_id="attempt-1", session_id="session-1", publication_id="legacy-publication",
                question_id="legacy-question-1", knowledge_point="source evidence", response_json={"answer": "fixture"},
                assessment="correct", hint_level=0, duration_ms=1000, created_at=now,
            ))

        # When the user plans cleanup, the exact protected references are disclosed as counts and block apply
        planned = self._plan()
        target = planned["manifest"]["targets"][0]
        self.assertFalse(target["eligibleToApply"])
        self.assertEqual(target["protectedRefs"]["assignments"], 1)
        self.assertEqual(target["protectedRefs"]["learningSessions"], 1)
        self.assertEqual(target["protectedRefs"]["exerciseAttempts"], 1)
        self.assertEqual(target["protectedRefs"]["batchQuestionAttempts"], 1)
        self.assertEqual(target["batchQuestions"][0]["attemptCount"], 1)
        backup_path = Path(tempfile.mkdtemp()) / "protected.json"
        with self.assertRaises(LegacyTextbookCleanupError):
            self.service.apply(
                planned["manifest"],
                expected_manifest_sha256=planned["manifestSha256"],
                expected_target_environment_fingerprint=planned["targetEnvironmentFingerprint"],
                backup_path=backup_path,
                now=2000.0,
            )

        # Then source rows, publication, assignment, session, and attempt remain byte-for-byte in place
        self.assertFalse(backup_path.exists())
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(upload_jobs.c.status).where(
                upload_jobs.c.upload_id == UPLOAD_ID
            )).scalar_one(), "completed")
            self.assertEqual(connection.execute(select(lesson_publications.c.status).where(
                lesson_publications.c.publication_id == "legacy-publication"
            )).scalar_one(), "published")
            self.assertEqual(connection.execute(select(exercise_attempts.c.attempt_id)).scalars().all(), ["attempt-1"])

    def test_user_blocks_manual_edit_and_unknown_revision_sources(self) -> None:
        # Given a legacy generated question revision, the human edit source must be recognized as protected
        with self.engine.begin() as connection:
            connection.execute(update(question_revisions).where(
                question_revisions.c.revision_id == "english-generated-question-revision"
            ).values(revision_source="manual_edit"))

        # When planning the real manual_edit source, the exact metadata exposes it and refuses withdrawal
        manual_plan = self._plan()
        manual_target = manual_plan["manifest"]["targets"][0]
        self.assertEqual(manual_target["protectedRefs"]["teacherEdits"], 1)
        self.assertEqual(manual_target["revisionSourceValues"], ["manual_edit"])
        self.assertFalse(manual_target["eligibleToApply"])

        # Given a future or corrupted revision source, then it also fails closed rather than being treated as generated
        with self.engine.begin() as connection:
            connection.execute(update(question_revisions).where(
                question_revisions.c.revision_id == "english-generated-question-revision"
            ).values(revision_source="future_editor_source"))
        unknown_plan = self._plan()
        unknown_target = unknown_plan["manifest"]["targets"][0]
        self.assertEqual(unknown_target["protectedRefs"]["unsupportedRevisionSources"], 1)
        self.assertEqual(unknown_target["revisionSourceValues"], ["future_editor_source"])
        self.assertFalse(unknown_target["eligibleToApply"])

    def test_user_sees_mastery_search_and_run_snapshot_references_before_cleanup(self) -> None:
        # Given all learner-facing evidence stores contain rows linked to the legacy publication
        now = 700.0
        with self.engine.begin() as connection:
            connection.execute(insert(lesson_publications).values(
                publication_id="legacy-evidence-publication",
                title="Legacy English",
                source_upload_id=UPLOAD_ID,
                lesson_ids_json=["english-lesson-01-01"],
                status="draft",
                version=1,
                created_at=now,
                updated_at=now,
            ))
            connection.execute(insert(knowledge_points).values(
                knowledge_point_id="legacy-evidence-kp",
                publication_id="legacy-evidence-publication",
                name="legacy concept",
                normalized_name="legacy concept",
                created_at=now,
            ))
            connection.execute(insert(mastery_states).values(
                learner_id="learner-fixture",
                knowledge_point_id="legacy-evidence-kp",
                score=0.5,
                raw_score=0.5,
                evidence_confidence=0.5,
                evidence_count=1,
                algorithm_version="mastery-v2",
                computed_at=now,
                attempt_count=1,
                correct_count=1,
            ))
            connection.execute(insert(lesson_search_documents).values(
                document_id="legacy-search-document",
                publication_id="legacy-evidence-publication",
                publication_version=1,
                lesson_id="english-lesson-01-01",
                question_id="legacy-question-1",
                source_question_key="legacy-question-1",
                source_pages_json="[]",
                title="legacy",
                chapter="chapter 1",
                knowledge_points_json="[]",
                body="protected search body",
                search_text="legacy protected search body",
                created_at=now,
                updated_at=now,
            ))
            connection.execute(insert(run_snapshots).values(
                run_id="legacy-run-snapshot",
                operation="initial_batch",
                scope="upload",
                target_upload_id=UPLOAD_ID,
                target_question_key=None,
                target_publication_id=None,
                status="succeeded",
                config_json={},
                result_json={},
                error_json=None,
                started_at=now,
                completed_at=now,
            ))

        # When planning, no learner-state or index count is hard-coded to zero and all such references block apply
        target = self._plan()["manifest"]["targets"][0]
        self.assertEqual(target["protectedRefs"]["masteryStates"], 1)
        self.assertEqual(target["protectedRefs"]["lessonSearchDocuments"], 1)
        self.assertEqual(target["protectedRefs"]["runSnapshots"], 1)
        self.assertEqual(target["lessonSearchDocumentIds"], ["legacy-search-document"])
        self.assertEqual(target["runSnapshotIds"], ["legacy-run-snapshot"])
        self.assertFalse(target["eligibleToApply"])

    def test_user_preserves_linked_frozen_quality_review_source_and_decisions(self) -> None:
        # Given a frozen quality-review package, case revision, and human decision linked to this source
        now = 800.0
        source_snapshot = {"uploadId": UPLOAD_ID, "sourceRevisionId": "english-source-revision-01"}
        case_snapshot = {"sourceRevisionId": "english-source-revision-01", "page": 23}
        with self.engine.begin() as connection:
            connection.execute(insert(quality_review_packages).values(
                package_id="legacy-quality-package",
                title="Frozen source review fixture",
                source_kind="real_open_material",
                source_json=source_snapshot,
                created_by="teacher-fixture",
                created_by_identity_verified=True,
                created_at=now,
            ))
            connection.execute(insert(quality_review_cases).values(
                case_id="legacy-quality-case",
                package_id="legacy-quality-package",
                external_case_id="case-1",
                ordinal=1,
                subject="english",
                source_page=23,
                source_kind="real_open_material",
                source_fingerprint="a" * 64,
                input_fingerprint="b" * 64,
                output_fingerprint="c" * 64,
                source_json=case_snapshot,
                locator_json={"page": 23},
                input_json={},
                expected_json={},
                actual_json={},
                record_version=1,
                review_status="human_accepted",
                source_verified_at=now,
                source_verified_hash="a" * 64,
                source_verified_quote="evidence preserved in fixture",
                counted=False,
                created_at=now,
            ))
            connection.execute(insert(quality_review_case_revisions).values(
                case_id="legacy-quality-case",
                record_version=1,
                source_fingerprint="a" * 64,
                input_fingerprint="b" * 64,
                output_fingerprint="c" * 64,
                source_json=case_snapshot,
                locator_json={"page": 23},
                input_json={},
                expected_json={},
                actual_json={},
                created_by="teacher-fixture",
                created_by_identity_verified=True,
                created_at=now,
            ))
            connection.execute(insert(quality_review_decisions).values(
                review_id="legacy-quality-decision",
                case_id="legacy-quality-case",
                reviewed_record_version=1,
                decision_stage="final",
                source_fingerprint="a" * 64,
                input_fingerprint="b" * 64,
                output_fingerprint="c" * 64,
                actor_id="teacher-fixture",
                actor_kind="teacher",
                identity_verified=True,
                display_name="teacher",
                decision="accepted",
                rationale="review retained",
                verified_quote="verified quote retained",
                claimed_quote="claimed quote retained",
                created_at=now,
            ))

        # When planning, every frozen artifact is identified and blocks withdrawal without modifying its body
        target = self._plan()["manifest"]["targets"][0]
        self.assertEqual(target["protectedRefs"]["qualityReviewPackages"], 1)
        self.assertEqual(target["protectedRefs"]["qualityReviews"], 1)
        self.assertEqual(target["protectedRefs"]["qualityReviewCaseRevisions"], 1)
        self.assertEqual(target["protectedRefs"]["qualityReviewDecisions"], 1)
        self.assertEqual(target["qualityReviewPackageIds"], ["legacy-quality-package"])
        self.assertEqual(target["qualityCaseIds"], ["legacy-quality-case"])
        self.assertFalse(target["eligibleToApply"])

    def test_user_cannot_restore_over_changed_source_or_chapter_content(self) -> None:
        # Given an applied cleanup with a private restorable CAS snapshot
        planned = self._plan()
        backup_path = Path(tempfile.mkdtemp()) / "source-cas-backup.json"
        self.service.apply(
            planned["manifest"],
            expected_manifest_sha256=planned["manifestSha256"],
            expected_target_environment_fingerprint=planned["targetEnvironmentFingerprint"],
            backup_path=backup_path,
            now=4000.0,
        )

        # When the preserved OCR cache changes after apply, restore refuses to claim the original source state
        ocr = self.upload_root / "assets/chapter-page-023/source.md"
        ocr.write_text("changed source cache", encoding="utf-8")
        with self.assertRaises(LegacyTextbookCleanupConflict):
            self.service.restore(
                backup_path,
                expected_manifest_sha256=planned["manifestSha256"],
                expected_target_environment_fingerprint=planned["targetEnvironmentFingerprint"],
                now=5000.0,
            )

    def test_user_cannot_restore_over_changed_legacy_chapter_body(self) -> None:
        # Given an applied cleanup snapshot whose chapter body remains CAS protected
        planned = self._plan()
        backup_path = Path(tempfile.mkdtemp()) / "chapter-cas-backup.json"
        self.service.apply(
            planned["manifest"],
            expected_manifest_sha256=planned["manifestSha256"],
            expected_target_environment_fingerprint=planned["targetEnvironmentFingerprint"],
            backup_path=backup_path,
            now=6000.0,
        )
        with self.engine.begin() as connection:
            chapter = connection.execute(select(chapter_records.c.chapter_json).where(
                chapter_records.c.chapter_id == "english-chapter-01"
            )).scalar_one()
            chapter["title"] = "changed while tombstoned"
            connection.execute(update(chapter_records).where(
                chapter_records.c.chapter_id == "english-chapter-01"
            ).values(chapter_json=chapter))

        # When restoration sees the body fingerprint change, it refuses to overwrite the newer record
        with self.assertRaises(LegacyTextbookCleanupConflict):
            self.service.restore(
                backup_path,
                expected_manifest_sha256=planned["manifestSha256"],
                expected_target_environment_fingerprint=planned["targetEnvironmentFingerprint"],
                now=7000.0,
            )


if __name__ == "__main__":
    unittest.main()
