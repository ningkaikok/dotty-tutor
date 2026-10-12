"""Reversible tombstones for obsolete textbook-derived legacy outputs.

The source upload is intentionally never deleted or rewritten. The cleanup
marker contains only exact identifiers and CAS metadata so readers can retire
old generated views while later source-only courseware can still use the same
PDF, OCR cache, and source revisions.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    Float,
    MetaData,
    String,
    Table,
    func,
    select,
    update,
)
from sqlalchemy.engine import Connection, Engine

from persistence.database import decode_json
from persistence.lesson_media_store import lesson_media_reviews, lesson_media_revisions
from persistence.quality_review_runs_store import (
    quality_review_run_cases,
    quality_review_run_ratings,
    quality_review_run_results,
    quality_review_runs,
)
from persistence.quality_review_store import (
    quality_review_case_revisions,
    quality_review_cases,
    quality_review_decisions,
    quality_review_packages,
)
from persistence.schema import (
    assignment_plans,
    assignments,
    background_jobs,
    batch_questions,
    chapter_english_attempts,
    chapter_records,
    exercise_attempts,
    json_document,
    knowledge_points,
    learning_sessions,
    lesson_documents,
    lesson_publications,
    mastery_states,
    question_revisions,
    run_snapshots,
    teacher_review_events,
    upload_jobs,
)
from persistence.search_store import lesson_search_documents
from persistence.voice_schema import voice_sessions

legacy_cleanup_metadata = MetaData()

legacy_textbook_cleanup_manifests = Table(
    "legacy_textbook_cleanup_manifests",
    legacy_cleanup_metadata,
    Column("manifest_id", String(64), primary_key=True),
    Column("upload_id", String(64), nullable=False, unique=True),
    Column("fingerprint", String(64), nullable=False),
    Column("snapshot_json", json_document, nullable=False),
    Column("status", String(16), nullable=False, default="applied"),
    Column("applied_at", Float, nullable=False),
    Column("restored_at", Float),
    CheckConstraint("status IN ('applied', 'restored')", name="ck_legacy_cleanup_manifest_status"),
)


class LegacyTextbookCleanupConflict(ValueError):
    """Raised when a stale snapshot or different cleanup manifest wins the CAS."""


def canonical_json(value: Any) -> str:
    """Serialize metadata deterministically for fingerprints and audit snapshots."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _chapter_content_fingerprint(chapter_json: dict[str, Any]) -> str:
    body = {key: value for key, value in chapter_json.items() if key not in {"recordVersion", "deletedAt"}}
    return _sha256_json(body)


def _as_dict(value: Any) -> dict[str, Any]:
    decoded = decode_json(value) if value is not None else {}
    return decoded if isinstance(decoded, dict) else {}


def _linked_metadata(value: Any, upload_id: str, revision_ids: set[str], hashes: set[str]) -> bool:
    """Match explicit provenance fields without matching arbitrary body text."""
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).replace("_", "").lower()
            if normalized in {"uploadid", "sourceuploadid"} and str(item) == upload_id:
                return True
            if normalized in {"sourcerevisionid", "revisionid"} and str(item) in revision_ids:
                return True
            if normalized in {"sourcefilesha256", "sourcesha256", "sourcefingerprint"} and str(item) in hashes:
                return True
            if _linked_metadata(item, upload_id, revision_ids, hashes):
                return True
    elif isinstance(value, list):
        return any(_linked_metadata(item, upload_id, revision_ids, hashes) for item in value)
    return False


def _job_references(
    payload: Any,
    upload_id: str,
    chapter_ids: set[str],
    lesson_ids: set[str],
    batch_ids: set[str],
    question_ids: set[str],
) -> tuple[bool, str | None, str | None, str | None, str | None, str | None]:
    found_upload: str | None = None
    found_chapter: str | None = None
    found_lesson: str | None = None
    found_batch: str | None = None
    found_question: str | None = None

    def visit(value: Any) -> None:
        nonlocal found_upload, found_chapter, found_lesson, found_batch, found_question
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key).replace("_", "").lower()
                if normalized == "uploadid" and str(item) == upload_id:
                    found_upload = upload_id
                elif normalized in {"chapterid", "chapterids"}:
                    values = item if isinstance(item, list) else [item]
                    found_chapter = next((str(value) for value in values if str(value) in chapter_ids), found_chapter)
                elif normalized in {"lessonid", "lessonids"}:
                    values = item if isinstance(item, list) else [item]
                    found_lesson = next((str(value) for value in values if str(value) in lesson_ids), found_lesson)
                elif normalized in {"batchid", "batchids"}:
                    values = item if isinstance(item, list) else [item]
                    found_batch = next((str(value) for value in values if str(value) in batch_ids), found_batch)
                elif normalized in {"questionid", "questionids"}:
                    values = item if isinstance(item, list) else [item]
                    found_question = next((str(value) for value in values if str(value) in question_ids), found_question)
                if isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, (dict, list)):
                    visit(item)

    visit(payload)
    return (
        bool(found_upload or found_chapter or found_lesson or found_batch or found_question),
        found_upload,
        found_chapter,
        found_lesson,
        found_batch,
        found_question,
    )


def _generated_ids(value: Any, wanted_keys: set[str]) -> set[str]:
    """Collect exact generated identifiers from the upload's retained result JSON."""
    found: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                normalized = str(key).replace("_", "").lower()
                if normalized in wanted_keys:
                    values = child if isinstance(child, list) else [child]
                    found.update(str(candidate) for candidate in values if isinstance(candidate, (str, int)))
                if isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                if isinstance(child, (dict, list)):
                    visit(child)

    visit(value)
    return found


def _source_fingerprint(inspection: dict[str, Any]) -> str:
    """Fingerprint source/OCR metadata and bytes separately from derived tombstones."""
    upload = inspection["upload"]
    return _sha256_json({
        key: upload.get(key) for key in (
            "uploadId", "filename", "status", "importId", "updatedAt", "sourceSha256",
            "sourceTextSha256", "resultMetadataFingerprint", "generatedOutputKeys", "resultKeys",
        )
    })


def _asset_tree_fingerprint(upload_root: Path) -> dict[str, Any]:
    """Hash stored source assets without decoding or returning their contents."""
    rows: list[tuple[str, int, str]] = []
    safe_root = not upload_root.is_symlink() and upload_root.is_dir()
    if safe_root:
        try:
            upload_root.resolve(strict=True).relative_to(upload_root.parent.resolve(strict=True))
        except (OSError, ValueError):
            safe_root = False
    if safe_root:
        for path in sorted(upload_root.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(upload_root.resolve())
            except (OSError, ValueError):
                continue
            digest = hashlib.sha256()
            size = 0
            with resolved.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            rows.append((path.relative_to(upload_root).as_posix(), size, digest.hexdigest()))
    tree_digest = _sha256_json(rows)
    source_file = upload_root / "source.pdf"
    source_hash = next((digest for name, _, digest in rows if name == "source.pdf"), None)
    return {
        "sourcePdfSha256": source_hash,
        "sourcePathRelative": f"uploads/{upload_root.name}/source.pdf",
        "assetRoots": [f"uploads/{upload_root.name}/assets"],
        "assetFileCount": len(rows),
        "assetBytes": sum(size for _, size, _ in rows),
        "assetTreeSha256": tree_digest,
        "sourcePdfExists": source_file.is_file() and source_hash is not None,
    }


class LegacyTextbookCleanupStore:
    """Inspect and tombstone one upload's exact legacy-derived content set."""

    def __init__(self, engine: Engine, *, data_root: str | Path | None = None) -> None:
        self.engine = engine
        configured_root = data_root or os.getenv("DOTTY_DATA_DIR")
        self.data_root = (
            Path(configured_root).expanduser().resolve()
            if configured_root
            else Path(__file__).resolve().parents[3] / "data"
        )

    def inspect_upload(self, upload_id: str) -> dict[str, Any]:
        """Return exact linked identifiers, safe metadata, reference counts and fingerprint."""
        if not upload_id or len(upload_id) > 64 or any(not (char.isalnum() or char in "_-") for char in upload_id):
            raise ValueError("uploadId 格式不合法")
        with self.engine.connect() as connection:
            result = self._inspect(connection, upload_id)
        fingerprint_input = {key: value for key, value in result.items() if key != "fingerprint"}
        result["fingerprint"] = _sha256_json(fingerprint_input)
        return result

    def _inspect(self, connection: Connection, upload_id: str) -> dict[str, Any]:
        upload_row = connection.execute(select(
            upload_jobs.c.upload_id,
            upload_jobs.c.import_id,
            upload_jobs.c.filename,
            upload_jobs.c.status,
            upload_jobs.c.updated_at,
            upload_jobs.c.source_text,
            upload_jobs.c.result_json,
        ).where(upload_jobs.c.upload_id == upload_id)).mappings().first()
        if upload_row is None:
            raise LookupError("目标教材上传不存在")

        result_json = _as_dict(upload_row["result_json"])
        material_kind = str(result_json.get("materialKind") or "unknown")
        result_lesson_ids = _generated_ids(result_json, {"lessonid", "lessonids"})
        result_batch_ids = _generated_ids(result_json, {"batchid", "batchids"})
        result_question_ids = _generated_ids(result_json, {"questionid", "questionids"})
        page_count = next((result_json.get(key) for key in (
            "sourcePageCount", "sourcePdfPageCount", "pageCount", "totalPages",
        ) if isinstance(result_json.get(key), int)), None)

        chapter_rows = connection.execute(select(
            chapter_records.c.chapter_id,
            chapter_records.c.status,
            chapter_records.c.chapter_json,
            chapter_records.c.record_version,
        ).order_by(chapter_records.c.chapter_id)).mappings().all()
        matching_chapters: list[dict[str, Any]] = []
        for row in chapter_rows:
            chapter_json = _as_dict(row["chapter_json"])
            if chapter_json.get("sourcePreparationJobId"):
                continue
            revisions = [
                revision for revision in chapter_json.get("sourceRevisions", [])
                if isinstance(revision, dict) and revision.get("uploadId") == upload_id
            ]
            if not revisions and chapter_json.get("sourceUploadId") != upload_id:
                continue
            revision_ids = {
                str(revision["sourceRevisionId"])
                for revision in revisions if revision.get("sourceRevisionId")
            }
            current_ids = {str(value) for value in chapter_json.get("currentLessonIds", [])}
            lesson_entries = [item for item in chapter_json.get("lessons", []) if isinstance(item, dict)]
            linked_entries = [
                item for item in lesson_entries
                if str(item.get("lessonId") or "") in current_ids
                or str(item.get("sourceRevisionId") or "") in revision_ids
                or not revision_ids
            ]
            linked_ids = sorted({
                str(item.get("lessonId")) for item in linked_entries if item.get("lessonId")
            } | current_ids)
            issues = chapter_json.get("reviewIssues", [])
            if not isinstance(issues, list):
                issues = []
            review_issue_count = sum(
                1 for item in issues
                if not isinstance(item, dict) or not item.get("lessonId") or str(item.get("lessonId")) in linked_ids
            )
            review_count = sum(
                len(item.get("reviews", [])) for item in linked_entries
                if isinstance(item.get("reviews", []), list)
            )
            source_hashes = sorted({
                str(revision.get("sourceFileSha256")) for revision in revisions
                if revision.get("sourceFileSha256")
            })
            ranges = [
                {"pageStart": revision.get("pageStart"), "pageEnd": revision.get("pageEnd")}
                for revision in revisions
            ]
            matching_chapters.append({
                "chapterId": str(row["chapter_id"]),
                "status": str(row["status"]),
                "recordVersion": int(row["record_version"]),
                "teachingMode": chapter_json.get("teachingMode"),
                "sourceRevisionIds": sorted(revision_ids),
                "sourceSha256": source_hashes[0] if len(source_hashes) == 1 else None,
                "pageStart": min((item["pageStart"] for item in ranges if isinstance(item["pageStart"], int)), default=None),
                "pageEnd": max((item["pageEnd"] for item in ranges if isinstance(item["pageEnd"], int)), default=None),
                "lessonIds": linked_ids,
                "reviewIssuesCount": review_issue_count,
                "reviewMetadataCount": review_count,
                "deletedAt": chapter_json.get("deletedAt"),
            })

        chapter_ids = {item["chapterId"] for item in matching_chapters}
        lesson_ids = {lesson_id for item in matching_chapters for lesson_id in item["lessonIds"]}
        lesson_ids.update(result_lesson_ids)
        lesson_rows = connection.execute(select(lesson_documents).where(
            (lesson_documents.c.source_upload_id == upload_id)
            | lesson_documents.c.lesson_id.in_(lesson_ids or {"__no_legacy_lessons__"})
        ).order_by(lesson_documents.c.lesson_id)).mappings().all()
        embedded_lessons: dict[str, dict[str, Any]] = {}
        for row in chapter_rows:
            chapter = _as_dict(row["chapter_json"])
            for item in chapter.get("lessons", []):
                if isinstance(item, dict) and item.get("lessonId") in lesson_ids:
                    embedded_lessons[str(item["lessonId"])] = item

        lesson_records = [dict(row) for row in lesson_rows]
        lesson_publication_rows = connection.execute(select(lesson_publications)).mappings().all()
        publications = []
        publication_ids: set[str] = set()
        for row in lesson_publication_rows:
            linked_lessons = set(decode_json(row["lesson_ids_json"]) or [])
            if row["source_upload_id"] == upload_id or linked_lessons.intersection(lesson_ids):
                publication_id = str(row["publication_id"])
                publication_ids.add(publication_id)
                publications.append({
                    "publicationId": publication_id,
                    "status": str(row["status"]),
                    "version": int(row["version"]),
                    "lessonIds": sorted(str(item) for item in linked_lessons),
                })

        assignments_rows = connection.execute(select(
            assignments.c.assignment_id, assignments.c.publication_id,
        ).where(assignments.c.publication_id.in_(publication_ids or {"__no_legacy_publications__"}))).mappings().all()
        assignment_ids = {str(row["assignment_id"]) for row in assignments_rows}
        assignment_plans_count = connection.execute(select(assignment_plans.c.plan_id).where(
            assignment_plans.c.publication_id.in_(publication_ids or {"__no_legacy_publications__"})
        )).scalars().all()
        sessions = connection.execute(select(
            learning_sessions.c.session_id,
            learning_sessions.c.publication_id,
            learning_sessions.c.assignment_id,
        ).where(
            learning_sessions.c.publication_id.in_(publication_ids or {"__no_legacy_publications__"})
            | learning_sessions.c.assignment_id.in_(assignment_ids or {"__no_legacy_assignments__"})
        )).mappings().all()
        session_ids = {str(row["session_id"]) for row in sessions}
        session_ids.add("__no_legacy_sessions__")

        chapter_attempt_count = len(connection.execute(select(
            chapter_english_attempts.c.attempt_id,
        ).where(chapter_english_attempts.c.publication_id.in_(
            publication_ids or {"__no_legacy_publications__"}
        ))).scalars().all())
        exercise_attempt_count = len(connection.execute(select(exercise_attempts.c.attempt_id).where(
            exercise_attempts.c.session_id.in_(session_ids)
            | exercise_attempts.c.publication_id.in_(publication_ids or {"__no_legacy_publications__"})
        )).scalars().all())

        revision_rows = connection.execute(select(
            question_revisions.c.revision_id,
            question_revisions.c.source_question_key,
            question_revisions.c.revision_source,
        ).where(question_revisions.c.upload_id == upload_id)).mappings().all()
        revision_counts: dict[str, int] = {}
        for row in revision_rows:
            key = str(row["source_question_key"])
            revision_counts[key] = revision_counts.get(key, 0) + 1
        batch_rows = connection.execute(select(batch_questions).where(
            batch_questions.c.upload_id == upload_id
        ).order_by(batch_questions.c.batch_id)).mappings().all()
        question_ids = set(result_question_ids)
        question_ids.update(str(row["question_id"]) for row in batch_rows)
        question_attempt_rows = connection.execute(select(
            exercise_attempts.c.question_id,
            func.count().label("attempt_count"),
        ).where(exercise_attempts.c.question_id.in_(
            sorted(question_ids) or ["__no_legacy_questions__"]
        )).group_by(exercise_attempts.c.question_id)).mappings().all()
        question_attempt_counts = {str(row["question_id"]): int(row["attempt_count"]) for row in question_attempt_rows}
        batch_questions_result = [{
            "batchId": str(row["batch_id"]),
            "questionId": str(row["question_id"]),
            "currentRevisionId": row["current_revision_id"],
            "revisionCount": revision_counts.get(str(row["question_id"]), 0),
            "attemptCount": question_attempt_counts.get(str(row["question_id"]), 0),
        } for row in batch_rows]

        revision_ids = {
            revision_id for chapter in matching_chapters for revision_id in chapter["sourceRevisionIds"]
        }
        source_hashes = {
            str(chapter["sourceSha256"]) for chapter in matching_chapters if chapter.get("sourceSha256")
        }
        job_rows = connection.execute(select(
            background_jobs.c.job_id,
            background_jobs.c.job_type,
            background_jobs.c.status,
            background_jobs.c.attempt_count,
            background_jobs.c.cancel_requested,
            background_jobs.c.lease_owner,
            background_jobs.c.lease_expires_at,
            background_jobs.c.payload_json,
        ).order_by(background_jobs.c.job_id)).mappings().all()
        jobs = []
        exact_batch_ids = {str(row["batch_id"]) for row in batch_rows} | result_batch_ids
        for row in job_rows:
            linked, linked_upload, linked_chapter, linked_lesson, linked_batch, linked_question = _job_references(
                decode_json(row["payload_json"]), upload_id, chapter_ids, lesson_ids,
                exact_batch_ids, question_ids,
            )
            if linked:
                jobs.append({
                    "jobId": str(row["job_id"]),
                    "jobType": str(row["job_type"]),
                    "status": str(row["status"]),
                    "attemptCount": int(row["attempt_count"]),
                    "cancelRequested": bool(row["cancel_requested"]),
                    "leaseOwner": row["lease_owner"],
                    "leaseExpiresAt": row["lease_expires_at"],
                    "uploadId": linked_upload,
                    "chapterId": linked_chapter,
                    "lessonId": linked_lesson,
                    "batchId": linked_batch,
                    "questionId": linked_question,
                })

        # Teacher-authored question revisions and chapter review decisions are preserved and block withdrawal.
        revision_source_values = sorted({str(row["revision_source"]).lower() for row in revision_rows})
        teacher_edit_count = sum(1 for row in revision_rows if str(row["revision_source"]).lower() == "manual_edit")
        unsupported_revision_source_count = sum(
            1 for row in revision_rows
            if str(row["revision_source"]).lower() not in {"model_generated", "manual_edit"}
        )
        teacher_review_count = sum(chapter["reviewMetadataCount"] for chapter in matching_chapters)
        for record in lesson_records:
            embedded = embedded_lessons.get(str(record["lesson_id"]), {})
            question = _as_dict(record["question_json"])
            teacher_review_count += len(question.get("reviews", [])) if isinstance(question.get("reviews", []), list) else 0
            teacher_edit_count += int(bool(question.get("teacherEdited") or question.get("revisionSource") in {"teacher_edit", "manual_edit"}))

        lesson_output = []
        for record in lesson_records:
            lesson_id = str(record["lesson_id"])
            embedded = embedded_lessons.get(lesson_id, {})
            reviews = embedded.get("reviews", []) if isinstance(embedded.get("reviews", []), list) else []
            question = _as_dict(record["question_json"])
            lesson_output.append({
                "lessonId": lesson_id,
                "sourceUploadId": record["source_upload_id"],
                "status": str(record["status"]),
                "version": int(record["version"]),
                "publicationIds": sorted(publication_id for publication_id in publication_ids
                                          if lesson_id in next((item["lessonIds"] for item in publications if item["publicationId"] == publication_id), [])),
                "attemptCount": chapter_attempt_count + exercise_attempt_count,
                "reviewCount": len(reviews) + len(question.get("reviews", [])),
                "reviewed": bool(reviews or question.get("reviews") or record["status"] in {"approved", "published"}),
                "teacherEditCount": int(bool(question.get("teacherEdited") or question.get("revisionSource") in {"teacher_edit", "manual_edit"})),
                "qualityReviewRefs": len(question.get("qualityReviewRefs", [])) if isinstance(question.get("qualityReviewRefs", []), list) else int(bool(question.get("qualityReviewRef"))),
            })

        packages = connection.execute(select(
            quality_review_packages.c.package_id, quality_review_packages.c.source_json,
        )).mappings().all()
        quality_package_ids = {
            str(row["package_id"]) for row in packages
            if _linked_metadata(decode_json(row["source_json"]), upload_id, revision_ids, source_hashes)
        }
        quality_case_rows = connection.execute(select(
            quality_review_cases.c.case_id,
            quality_review_cases.c.package_id,
            quality_review_cases.c.source_json,
        )).mappings().all()
        quality_case_ids = {
            str(row["case_id"]) for row in quality_case_rows
            if str(row["package_id"]) in quality_package_ids
            or _linked_metadata(decode_json(row["source_json"]), upload_id, revision_ids, source_hashes)
        }
        quality_case_revision_rows = connection.execute(select(
            quality_review_case_revisions.c.case_id,
            quality_review_case_revisions.c.record_version,
        ).where(quality_review_case_revisions.c.case_id.in_(
            quality_case_ids or {"__no_legacy_quality_cases__"}
        ))).mappings().all()
        quality_decision_rows = connection.execute(select(
            quality_review_decisions.c.review_id,
            quality_review_decisions.c.case_id,
        ).where(quality_review_decisions.c.case_id.in_(
            quality_case_ids or {"__no_legacy_quality_cases__"}
        ))).mappings().all()
        quality_run_case_rows = connection.execute(select(
            quality_review_run_cases.c.run_id,
            quality_review_run_cases.c.case_id,
        ).where(quality_review_run_cases.c.case_id.in_(
            quality_case_ids or {"__no_legacy_quality_cases__"}
        ))).mappings().all()
        quality_run_ids = {str(row["run_id"]) for row in quality_run_case_rows}
        quality_run_rows = connection.execute(select(
            quality_review_runs.c.run_id,
        ).where(quality_review_runs.c.run_id.in_(quality_run_ids or {"__no_legacy_quality_runs__"}))).scalars().all()
        quality_run_result_count = len(connection.execute(select(
            quality_review_run_results.c.result_id,
        ).where(quality_review_run_results.c.run_id.in_(
            quality_run_ids or {"__no_legacy_quality_runs__"}
        ))).scalars().all())
        quality_run_rating_count = len(connection.execute(select(
            quality_review_run_ratings.c.rating_id,
        ).where(quality_review_run_ratings.c.run_id.in_(
            quality_run_ids or {"__no_legacy_quality_runs__"}
        ))).scalars().all())

        media_rows = connection.execute(select(
            lesson_media_revisions.c.media_id,
            lesson_media_revisions.c.chapter_id,
            lesson_media_revisions.c.lesson_id,
            lesson_media_revisions.c.publication_id,
            lesson_media_revisions.c.source_revision_id,
            lesson_media_revisions.c.source_snapshot_json,
        )).mappings().all()
        media_ids = {
            str(row["media_id"]) for row in media_rows
            if str(row["chapter_id"]) in chapter_ids
            or str(row["lesson_id"]) in {record["lessonId"] for record in lesson_output}
            or str(row["publication_id"]) in publication_ids
            or str(row["source_revision_id"]) in revision_ids
            or _linked_metadata(decode_json(row["source_snapshot_json"]), upload_id, revision_ids, source_hashes)
        }
        media_review_count = len(connection.execute(select(lesson_media_reviews.c.review_id).where(
            lesson_media_reviews.c.media_id.in_(media_ids or {"__no_legacy_media__"})
        )).scalars().all())

        voice_rows = connection.execute(select(
            voice_sessions.c.session_id,
            voice_sessions.c.chapter_id,
            voice_sessions.c.publication_id,
            voice_sessions.c.lesson_id,
        )).mappings().all()
        lesson_id_set = {record["lessonId"] for record in lesson_output}
        voice_ids = {
            str(row["session_id"]) for row in voice_rows
            if str(row["chapter_id"]) in chapter_ids
            or str(row["publication_id"]) in publication_ids
            or str(row["lesson_id"]) in lesson_id_set
        }

        review_event_count = len(connection.execute(select(teacher_review_events.c.event_id).where(
            teacher_review_events.c.assignment_id.in_(assignment_ids or {"__no_legacy_assignments__"})
        )).scalars().all())

        from persistence.courseware_store import (
            courseware_publications,
            courseware_records,
        )

        new_courseware_rows = connection.execute(select(
            courseware_records.c.record_id,
            courseware_records.c.courseware_id,
            courseware_records.c.chapter_id,
            courseware_records.c.record_type,
            courseware_records.c.source_revision_id,
        ).where(
            courseware_records.c.chapter_id.in_(chapter_ids or {"__no_legacy_chapters__"})
            | courseware_records.c.source_revision_id.in_(revision_ids or {"__no_legacy_revisions__"})
        )).mappings().all()
        courseware_ids = {str(row["courseware_id"]) for row in new_courseware_rows if row.get("courseware_id")}
        courseware_publication_count = len(connection.execute(select(
            courseware_publications.c.publication_id,
        ).where(courseware_publications.c.courseware_id.in_(courseware_ids or {"__no_legacy_courseware__"}))).scalars().all())

        upload_root = self.data_root / "uploads" / upload_id
        asset_fingerprint = _asset_tree_fingerprint(upload_root)
        source_revision_hashes = sorted({
            str(chapter["sourceSha256"]) for chapter in matching_chapters if chapter.get("sourceSha256")
        })
        source_sha = asset_fingerprint["sourcePdfSha256"] or (source_revision_hashes[0] if len(source_revision_hashes) == 1 else None)
        run_snapshot_ids = connection.execute(select(run_snapshots.c.run_id).where(
            run_snapshots.c.target_upload_id == upload_id
        )).scalars().all()
        result_keys = sorted(str(key) for key in result_json)
        question_ids.update(str(item["questionId"]) for item in batch_questions_result if item.get("questionId"))
        search_rows = connection.execute(select(
            lesson_search_documents.c.document_id,
            lesson_search_documents.c.lesson_id,
            lesson_search_documents.c.question_id,
            lesson_search_documents.c.publication_id,
        ).where(
            lesson_search_documents.c.lesson_id.in_(lesson_ids or {"__no_legacy_lessons__"})
            | lesson_search_documents.c.question_id.in_(question_ids or {"__no_legacy_questions__"})
            | lesson_search_documents.c.publication_id.in_(publication_ids or {"__no_legacy_publications__"})
        )).mappings().all()
        search_document_ids = {str(row["document_id"]) for row in search_rows}
        mastery_state_count = len(connection.execute(select(
            mastery_states.c.learner_id,
            mastery_states.c.knowledge_point_id,
        ).select_from(mastery_states.join(
            knowledge_points,
            mastery_states.c.knowledge_point_id == knowledge_points.c.knowledge_point_id,
        )).where(knowledge_points.c.publication_id.in_(
            publication_ids or {"__no_legacy_publications__"}
        ))).all())
        protected_refs = {
            "publications": len(publications),
            "assignments": len(assignments_rows),
            "assignmentPlans": len(assignment_plans_count),
            "learningSessions": len(sessions),
            "exerciseAttempts": exercise_attempt_count,
            "chapterAttempts": chapter_attempt_count,
            "questionRevisions": len(revision_rows),
            "teacherEdits": teacher_edit_count,
            "unsupportedRevisionSources": unsupported_revision_source_count,
            "teacherReviews": teacher_review_count,
            "teacherReviewEvents": review_event_count,
            "qualityReviews": len(quality_case_ids),
            "qualityReviewPackages": len(quality_package_ids),
            "qualityReviewCaseRevisions": len(quality_case_revision_rows),
            "qualityReviewDecisions": len(quality_decision_rows),
            "qualityReviewRunCases": len(quality_run_case_rows),
            "qualityReviewRuns": len(quality_run_rows),
            "qualityReviewRunResults": quality_run_result_count,
            "qualityReviewRunRatings": quality_run_rating_count,
            "mediaRevisions": len(media_ids),
            "mediaReviews": media_review_count,
            "voiceSessions": len(voice_ids),
            "coursewareRecords": len(new_courseware_rows),
            "coursewarePublications": courseware_publication_count,
            "runSnapshots": len(run_snapshot_ids),
            "masteryStates": mastery_state_count,
            "lessonSearchDocuments": len(search_document_ids),
            "batchQuestionAttempts": sum(question_attempt_counts.values()),
        }
        result = {
            "upload": {
                "uploadId": str(upload_row["upload_id"]),
                "filename": str(upload_row["filename"]),
                "status": str(upload_row["status"]),
                "importId": upload_row["import_id"],
                "updatedAt": float(upload_row["updated_at"]),
                "sourceSha256": source_sha,
                "sourcePageCount": page_count,
                "materialKind": material_kind,
                "sourceTextSha256": hashlib.sha256(str(upload_row["source_text"] or "").encode("utf-8")).hexdigest(),
                "resultMetadataFingerprint": _sha256_json(result_json),
                "generatedOutputKeys": sorted(key for key in result_json if key in {
                    "questionPayload", "questionPayloads", "firstBatchJson", "firstBatch", "batchPayloads",
                    "generatedQuestions", "generatedLessons", "lessonIds",
                }),
                "resultKeys": result_keys,
            },
            "chapters": matching_chapters,
            "lessons": lesson_output,
            "batchQuestions": batch_questions_result,
            "publications": publications,
            "jobs": jobs,
            "protectedRefs": protected_refs,
            "assetFingerprint": asset_fingerprint,
        }
        result["chapterIds"] = sorted(chapter_ids)
        result["lessonIds"] = sorted(lesson_id_set | lesson_ids)
        result["batchIds"] = sorted(exact_batch_ids)
        result["questionIds"] = sorted(question_ids)
        result["questionRevisionIds"] = sorted(str(row["revision_id"]) for row in revision_rows)
        result["revisionSourceValues"] = revision_source_values
        result["runSnapshotIds"] = sorted(str(item) for item in run_snapshot_ids)
        result["lessonSearchDocumentIds"] = sorted(search_document_ids)
        result["jobIds"] = sorted(item["jobId"] for item in jobs)
        result["qualityCaseIds"] = sorted(quality_case_ids)
        result["qualityReviewPackageIds"] = sorted(quality_package_ids)
        result["qualityCaseRevisionIds"] = sorted(
            f"{row['case_id']}:{row['record_version']}" for row in quality_case_revision_rows
        )
        result["qualityDecisionIds"] = sorted(str(row["review_id"]) for row in quality_decision_rows)
        result["qualityReviewRunIds"] = sorted(quality_run_ids)
        result["mediaIds"] = sorted(media_ids)
        result["voiceSessionIds"] = sorted(voice_ids)
        result["coursewareRecordIds"] = sorted(str(row["record_id"]) for row in new_courseware_rows)
        return result

    def snapshot_upload(
        self,
        upload_id: str,
        *,
        expected_fingerprint: str,
        manifest_id: str,
        confirmed_material_kind: str | None = None,
    ) -> dict[str, Any]:
        """Capture exact tombstone preconditions without copying textbook or generated bodies."""
        inspection = self.inspect_upload(upload_id)
        if inspection["fingerprint"] != expected_fingerprint:
            raise LegacyTextbookCleanupConflict("目标材料自计划后已变化，不能创建快照")
        if inspection["upload"]["materialKind"] == "paper":
            raise LegacyTextbookCleanupConflict("试卷不能作为旧教材产物清理")
        if inspection["upload"]["materialKind"] != "textbook" and confirmed_material_kind != "textbook":
            raise LegacyTextbookCleanupConflict("未识别的材料类型需要教师明确确认为教材")
        if any(chapter["deletedAt"] is not None for chapter in inspection["chapters"]):
            raise LegacyTextbookCleanupConflict("目标已包含清理墓碑，需使用原 manifest 恢复")
        current_chapters = []
        with self.engine.connect() as connection:
            for chapter in inspection["chapters"]:
                row = connection.execute(select(
                    chapter_records.c.status,
                    chapter_records.c.chapter_json,
                ).where(chapter_records.c.chapter_id == chapter["chapterId"])).mappings().one()
                chapter_json = _as_dict(row["chapter_json"])
                current_chapters.append({
                    "chapterId": chapter["chapterId"],
                    "priorRecordVersion": chapter["recordVersion"],
                    "deletedRecordVersion": chapter["recordVersion"] + 1,
                    "priorStatus": str(row["status"]),
                    "priorDeletedAt": chapter_json.get("deletedAt"),
                    "priorChapterFingerprint": _chapter_content_fingerprint(chapter_json),
                })
        previous_upload = inspection["upload"]
        return {
            "snapshotId": _sha256_json({"manifestId": manifest_id, "uploadId": upload_id, "fingerprint": expected_fingerprint}),
            "manifestId": manifest_id,
            "uploadId": upload_id,
            "fingerprint": expected_fingerprint,
            "previousUploadStatus": previous_upload["status"],
            "previousUploadUpdatedAt": previous_upload["updatedAt"],
            "chapterIds": inspection["chapterIds"],
            "lessonIds": inspection["lessonIds"],
            "batchIds": inspection["batchIds"],
            "questionIds": inspection["questionIds"],
            "questionRevisionIds": inspection["questionRevisionIds"],
            "jobIds": inspection["jobIds"],
            "runSnapshotIds": inspection["runSnapshotIds"],
            "lessonSearchDocumentIds": inspection["lessonSearchDocumentIds"],
            "sourceRevisionIds": sorted({
                revision_id for chapter in inspection["chapters"] for revision_id in chapter["sourceRevisionIds"]
            }),
            "sourcePdfSha256": inspection["assetFingerprint"].get("sourcePdfSha256"),
            "assetTreeSha256": inspection["assetFingerprint"].get("assetTreeSha256"),
            "uploadSourceFingerprint": _source_fingerprint(inspection),
            "confirmedMaterialKind": confirmed_material_kind,
            "changedChapters": current_chapters,
            "cancelledJobs": [
                {
                    "jobId": job["jobId"],
                    "priorStatus": job["status"],
                    "priorCancelRequested": job["cancelRequested"],
                    "priorLeaseOwner": job["leaseOwner"],
                    "priorLeaseExpiresAt": job["leaseExpiresAt"],
                }
                for job in inspection["jobs"] if job["status"] in {"queued", "running"}
            ],
        }

    def apply_upload_cleanup(
        self,
        snapshot: dict[str, Any],
        *,
        manifest_id: str | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        """CAS-tombstone old chapters and cancel only their exact active jobs."""
        chosen_manifest = manifest_id or str(snapshot.get("manifestId") or "")
        upload_id = str(snapshot.get("uploadId") or "")
        timestamp = float(now if now is not None else time.time())
        if not chosen_manifest or not upload_id:
            raise LegacyTextbookCleanupConflict("清理快照缺少 manifest 或 upload 标识")

        with self.engine.begin() as connection:
            # Reference writers must take a compatible shared lock on this row and
            # inspect the active marker in the same transaction. With READ COMMITTED,
            # a writer that commits first is visible to the following inspection; a
            # writer arriving later waits and then sees the marker this transaction adds.
            upload_row = connection.execute(select(
                upload_jobs.c.status,
                upload_jobs.c.updated_at,
            ).where(upload_jobs.c.upload_id == upload_id).with_for_update()).mappings().first()
            if upload_row is None:
                raise LookupError("目标教材上传不存在")
            marker = connection.execute(select(legacy_textbook_cleanup_manifests).where(
                legacy_textbook_cleanup_manifests.c.upload_id == upload_id
            ).with_for_update()).mappings().first()
            if marker:
                if marker["manifest_id"] != chosen_manifest:
                    raise LegacyTextbookCleanupConflict("目标已被另一个 manifest 清理")
                if marker["status"] == "restored":
                    raise LegacyTextbookCleanupConflict("该 manifest 已恢复，不能再次应用")
                saved_snapshot = _as_dict(marker["snapshot_json"])
                if saved_snapshot.get("fingerprint") != snapshot.get("fingerprint"):
                    raise LegacyTextbookCleanupConflict("manifest 墓碑快照与当前清理请求不匹配")
                inspection = self._inspect(connection, upload_id)
                if _source_fingerprint(inspection) != saved_snapshot.get("uploadSourceFingerprint"):
                    raise LegacyTextbookCleanupConflict("来源 PDF、OCR 或上传结果已变化，拒绝幂等确认")
                if inspection["assetFingerprint"].get("assetTreeSha256") != saved_snapshot.get("assetTreeSha256"):
                    raise LegacyTextbookCleanupConflict("来源素材文件已变化，拒绝幂等确认")
                for chapter in saved_snapshot["changedChapters"]:
                    row = connection.execute(select(
                        chapter_records.c.chapter_json,
                        chapter_records.c.record_version,
                    ).where(chapter_records.c.chapter_id == chapter["chapterId"])).mappings().first()
                    if row is None or int(row["record_version"]) != chapter["deletedRecordVersion"]:
                        raise LegacyTextbookCleanupConflict("清理章节版本已变化，拒绝幂等确认")
                    chapter_json = _as_dict(row["chapter_json"])
                    if chapter_json.get("deletedAt") != saved_snapshot.get("appliedAt"):
                        raise LegacyTextbookCleanupConflict("清理章节墓碑已变化，拒绝幂等确认")
                    if _chapter_content_fingerprint(chapter_json) != chapter["priorChapterFingerprint"]:
                        raise LegacyTextbookCleanupConflict("清理章节内容已变化，拒绝幂等确认")
                return self._apply_result(saved_snapshot, idempotent=True)

            inspection = self._inspect(connection, upload_id)
            expected_fingerprint = str(snapshot.get("fingerprint") or "")
            fingerprint_input = {key: value for key, value in inspection.items() if key != "fingerprint"}
            actual_fingerprint = _sha256_json(fingerprint_input)
            if actual_fingerprint != expected_fingerprint:
                raise LegacyTextbookCleanupConflict("目标材料指纹已变化，拒绝应用旧快照")
            if inspection["upload"]["materialKind"] == "paper":
                raise LegacyTextbookCleanupConflict("试卷不能作为旧教材产物清理")
            if inspection["upload"]["materialKind"] != "textbook" and snapshot.get("confirmedMaterialKind") != "textbook":
                raise LegacyTextbookCleanupConflict("未识别的材料类型需要教师明确确认为教材")
            protected = {
                key: count for key, count in inspection["protectedRefs"].items()
                if count and key != "questionRevisions"
            }
            if protected:
                raise LegacyTextbookCleanupConflict("目标存在必须保留的审核、发布、作答或媒体引用")
            if inspection["chapterIds"] != snapshot.get("chapterIds"):
                raise LegacyTextbookCleanupConflict("章节集合已变化，拒绝应用旧快照")
            if inspection["lessonIds"] != snapshot.get("lessonIds"):
                raise LegacyTextbookCleanupConflict("lesson 集合已变化，拒绝应用旧快照")
            if inspection["batchIds"] != snapshot.get("batchIds"):
                raise LegacyTextbookCleanupConflict("试题批次集合已变化，拒绝应用旧快照")
            if inspection["questionIds"] != snapshot.get("questionIds"):
                raise LegacyTextbookCleanupConflict("题目集合已变化，拒绝应用旧快照")
            if inspection["jobIds"] != snapshot.get("jobIds"):
                raise LegacyTextbookCleanupConflict("后台任务集合已变化，拒绝应用旧快照")
            if inspection["questionRevisionIds"] != snapshot.get("questionRevisionIds"):
                raise LegacyTextbookCleanupConflict("题目修订集合已变化，拒绝应用旧快照")
            if inspection["runSnapshotIds"] != snapshot.get("runSnapshotIds"):
                raise LegacyTextbookCleanupConflict("运行历史集合已变化，拒绝应用旧快照")
            if inspection["lessonSearchDocumentIds"] != snapshot.get("lessonSearchDocumentIds"):
                raise LegacyTextbookCleanupConflict("搜索索引集合已变化，拒绝应用旧快照")

            if upload_row["status"] != snapshot["previousUploadStatus"] or float(upload_row["updated_at"]) != float(snapshot["previousUploadUpdatedAt"]):
                raise LegacyTextbookCleanupConflict("来源上传状态已变化，拒绝清理")

            changed = []
            for chapter in snapshot["changedChapters"]:
                row = connection.execute(select(
                    chapter_records.c.status,
                    chapter_records.c.chapter_json,
                    chapter_records.c.record_version,
                ).where(chapter_records.c.chapter_id == chapter["chapterId"]).with_for_update()).mappings().first()
                if row is None or int(row["record_version"]) != chapter["priorRecordVersion"]:
                    raise LegacyTextbookCleanupConflict("章节版本已变化，拒绝清理")
                chapter_json = _as_dict(row["chapter_json"])
                if chapter_json.get("deletedAt") != chapter["priorDeletedAt"]:
                    raise LegacyTextbookCleanupConflict("章节删除标志已变化，拒绝清理")
                if _chapter_content_fingerprint(chapter_json) != chapter["priorChapterFingerprint"]:
                    raise LegacyTextbookCleanupConflict("章节内容已变化，拒绝清理")
                chapter_json["deletedAt"] = timestamp
                chapter_json["recordVersion"] = chapter["deletedRecordVersion"]
                updated = connection.execute(update(chapter_records).where(
                    chapter_records.c.chapter_id == chapter["chapterId"],
                    chapter_records.c.record_version == chapter["priorRecordVersion"],
                ).values(
                    chapter_json=chapter_json,
                    record_version=chapter["deletedRecordVersion"],
                    updated_at=timestamp,
                ))
                if updated.rowcount != 1:
                    raise LegacyTextbookCleanupConflict("章节 CAS 失败，拒绝清理")
                changed.append({
                    "chapterId": chapter["chapterId"],
                    "priorRecordVersion": chapter["priorRecordVersion"],
                    "deletedRecordVersion": chapter["deletedRecordVersion"],
                    "priorStatus": chapter["priorStatus"],
                    "priorDeletedAt": chapter["priorDeletedAt"],
                    "priorChapterFingerprint": chapter["priorChapterFingerprint"],
                })

            cancelled = []
            for job in snapshot["cancelledJobs"]:
                row = connection.execute(select(
                    background_jobs.c.status,
                    background_jobs.c.cancel_requested,
                    background_jobs.c.lease_owner,
                    background_jobs.c.lease_expires_at,
                ).where(background_jobs.c.job_id == job["jobId"]).with_for_update()).mappings().first()
                if row is None or row["status"] != job["priorStatus"]:
                    raise LegacyTextbookCleanupConflict("后台任务状态已变化，拒绝清理")
                if (
                    bool(row["cancel_requested"]) != bool(job["priorCancelRequested"])
                    or row["lease_owner"] != job["priorLeaseOwner"]
                    or row["lease_expires_at"] != job["priorLeaseExpiresAt"]
                ):
                    raise LegacyTextbookCleanupConflict("后台任务租约已变化，拒绝清理")
                prior = {
                    "jobId": job["jobId"],
                    "priorStatus": str(row["status"]),
                    "priorCancelRequested": bool(row["cancel_requested"]),
                    "priorLeaseOwner": row["lease_owner"],
                    "priorLeaseExpiresAt": row["lease_expires_at"],
                }
                if row["status"] == "queued":
                    connection.execute(update(background_jobs).where(
                        background_jobs.c.job_id == job["jobId"],
                        background_jobs.c.status == "queued",
                    ).values(
                        status="cancelled",
                        cancel_requested=True,
                        completed_at=timestamp,
                        updated_at=timestamp,
                    ))
                else:
                    connection.execute(update(background_jobs).where(
                        background_jobs.c.job_id == job["jobId"],
                        background_jobs.c.status == "running",
                    ).values(cancel_requested=True, updated_at=timestamp))
                cancelled.append(prior)

            applied_snapshot = {
                **snapshot,
                "manifestId": chosen_manifest,
                "appliedAt": timestamp,
                "changedChapters": changed,
                "cancelledJobs": cancelled,
            }
            connection.execute(legacy_textbook_cleanup_manifests.insert().values(
                manifest_id=chosen_manifest,
                upload_id=upload_id,
                fingerprint=expected_fingerprint,
                snapshot_json=applied_snapshot,
                status="applied",
                applied_at=timestamp,
                restored_at=None,
            ))
            return self._apply_result(applied_snapshot)

    def restore_upload(
        self,
        snapshot: dict[str, Any],
        *,
        manifest_id: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Restore only this manifest's tombstones, keeping cancelled work terminal."""
        upload_id = str(snapshot.get("uploadId") or "")
        timestamp = float(now if now is not None else time.time())
        if not upload_id or not manifest_id:
            raise LegacyTextbookCleanupConflict("恢复快照缺少 manifest 或 upload 标识")
        with self.engine.begin() as connection:
            marker = connection.execute(select(legacy_textbook_cleanup_manifests).where(
                legacy_textbook_cleanup_manifests.c.upload_id == upload_id
            ).with_for_update()).mappings().first()
            if marker is None or marker["manifest_id"] != manifest_id:
                raise LegacyTextbookCleanupConflict("未找到与此恢复快照匹配的清理墓碑")
            saved_snapshot = _as_dict(marker["snapshot_json"])
            if saved_snapshot.get("fingerprint") != snapshot.get("fingerprint"):
                raise LegacyTextbookCleanupConflict("恢复快照指纹与清理墓碑不一致")
            if marker["status"] == "restored":
                return {"status": "restored", "manifestId": manifest_id, "uploadId": upload_id,
                        "restoredChapters": saved_snapshot.get("restoredChapters", []), "idempotent": True,
                        "chapterIds": saved_snapshot.get("chapterIds", []),
                        "lessonIds": saved_snapshot.get("lessonIds", []),
                        "batchIds": saved_snapshot.get("batchIds", []),
                        "jobIds": saved_snapshot.get("jobIds", [])}

            inspection = self._inspect(connection, upload_id)
            if _source_fingerprint(inspection) != saved_snapshot.get("uploadSourceFingerprint"):
                raise LegacyTextbookCleanupConflict("来源 PDF、OCR 或上传结果已变化，拒绝恢复")
            if inspection["assetFingerprint"].get("sourcePdfSha256") != saved_snapshot.get("sourcePdfSha256"):
                raise LegacyTextbookCleanupConflict("来源 PDF hash 已变化，拒绝恢复")
            if inspection["assetFingerprint"].get("assetTreeSha256") != saved_snapshot.get("assetTreeSha256"):
                raise LegacyTextbookCleanupConflict("来源素材文件已变化，拒绝恢复")

            for chapter in saved_snapshot["changedChapters"]:
                row = connection.execute(select(
                    chapter_records.c.chapter_json,
                    chapter_records.c.record_version,
                ).where(chapter_records.c.chapter_id == chapter["chapterId"]).with_for_update()).mappings().first()
                if row is None or int(row["record_version"]) != chapter["deletedRecordVersion"]:
                    raise LegacyTextbookCleanupConflict("章节已在清理后被修改，拒绝覆盖恢复")
                chapter_json = _as_dict(row["chapter_json"])
                if chapter_json.get("deletedAt") != saved_snapshot["appliedAt"]:
                    raise LegacyTextbookCleanupConflict("章节墓碑已变化，拒绝覆盖恢复")
                if _chapter_content_fingerprint(chapter_json) != chapter["priorChapterFingerprint"]:
                    raise LegacyTextbookCleanupConflict("章节内容已变化，拒绝覆盖恢复")

            restored = []
            for chapter in saved_snapshot["changedChapters"]:
                row = connection.execute(select(chapter_records.c.chapter_json).where(
                    chapter_records.c.chapter_id == chapter["chapterId"]
                )).mappings().one()
                chapter_json = _as_dict(row["chapter_json"])
                chapter_json.pop("deletedAt", None)
                new_version = chapter["deletedRecordVersion"] + 1
                connection.execute(update(chapter_records).where(
                    chapter_records.c.chapter_id == chapter["chapterId"],
                    chapter_records.c.record_version == chapter["deletedRecordVersion"],
                ).values(
                    chapter_json=chapter_json,
                    record_version=new_version,
                    updated_at=timestamp,
                ))
                restored.append({"chapterId": chapter["chapterId"], "recordVersion": new_version})
            connection.execute(update(legacy_textbook_cleanup_manifests).where(
                legacy_textbook_cleanup_manifests.c.manifest_id == manifest_id,
                legacy_textbook_cleanup_manifests.c.status == "applied",
            ).values(status="restored", restored_at=timestamp))
            saved_snapshot["restoredAt"] = timestamp
            saved_snapshot["restoredChapters"] = restored
            connection.execute(update(legacy_textbook_cleanup_manifests).where(
                legacy_textbook_cleanup_manifests.c.manifest_id == manifest_id,
            ).values(snapshot_json=saved_snapshot))
            return {"status": "restored", "manifestId": manifest_id, "uploadId": upload_id,
                    "restoredChapters": restored, "idempotent": False,
                    "chapterIds": saved_snapshot.get("chapterIds", []),
                    "lessonIds": saved_snapshot.get("lessonIds", []),
                    "batchIds": saved_snapshot.get("batchIds", []),
                    "jobIds": saved_snapshot.get("jobIds", [])}

    @staticmethod
    def _apply_result(snapshot: dict[str, Any], *, idempotent: bool = False) -> dict[str, Any]:
        return {
            "status": "deleted",
            "manifestId": snapshot["manifestId"],
            "uploadId": snapshot["uploadId"],
            "fingerprint": snapshot["fingerprint"],
            "chapterIds": snapshot.get("chapterIds", []),
            "lessonIds": snapshot.get("lessonIds", []),
            "batchIds": snapshot.get("batchIds", []),
            "questionIds": snapshot.get("questionIds", []),
            "jobIds": snapshot.get("jobIds", []),
            "changedChapters": snapshot["changedChapters"],
            "cancelledJobs": snapshot["cancelledJobs"],
            "idempotent": idempotent,
        }


__all__ = [
    "LegacyTextbookCleanupConflict",
    "LegacyTextbookCleanupStore",
    "legacy_cleanup_metadata",
    "legacy_textbook_cleanup_manifests",
]
