"""PostgreSQL storage for append-only lesson media versions and reviews."""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    select,
    update,
)

from persistence.base import DatabaseStore
from persistence.database import decode_json
from persistence.schema import json_document

lesson_media_metadata = MetaData()

lesson_media_revisions = Table(
    "lesson_media_revisions",
    lesson_media_metadata,
    Column("media_id", String(64), primary_key=True),
    Column("series_id", String(64), nullable=False),
    Column("version", Integer, nullable=False),
    Column("chapter_id", String(64), nullable=False),
    Column("publication_id", String(64), nullable=False),
    Column("lesson_id", String(128), nullable=False),
    Column("source_revision_id", String(64), nullable=False),
    Column("source_fingerprint", String(64), nullable=False),
    Column("title", Text, nullable=False),
    Column("subject", String(16), nullable=False),
    Column("license", String(256), nullable=False),
    Column("source_snapshot_json", json_document, nullable=False),
    Column("scenes_json", json_document, nullable=False),
    Column("renderer_snapshot_json", json_document, nullable=False),
    Column("status", String(40), nullable=False),
    Column("distribution_scope", String(16), nullable=False),
    Column("artifact_path", Text),
    Column("captions_path", Text),
    Column("artifact_sha256", String(64)),
    Column("captions_sha256", String(64)),
    Column("duration_seconds", Float),
    Column("consistency_review_json", json_document),
    Column("published_at", Float),
    Column("created_by", String(128), nullable=False),
    Column("created_at", Float, nullable=False),
    UniqueConstraint("series_id", "version", name="uq_lesson_media_series_version"),
    CheckConstraint("version > 0", name="ck_lesson_media_version_positive"),
    CheckConstraint("subject IN ('math', 'english', 'science')", name="ck_lesson_media_subject"),
    CheckConstraint("status IN ('draft', 'rendering', 'consistency_review_required', 'consistency_failed', 'changes_requested', 'approved', 'published')", name="ck_lesson_media_status"),
    CheckConstraint("distribution_scope IN ('student', 'local_only')", name="ck_lesson_media_distribution_scope"),
)
Index("ix_lesson_media_series_version", lesson_media_revisions.c.series_id, lesson_media_revisions.c.version)
Index("ix_lesson_media_student_lookup", lesson_media_revisions.c.chapter_id, lesson_media_revisions.c.publication_id, lesson_media_revisions.c.lesson_id, lesson_media_revisions.c.status)

lesson_media_reviews = Table(
    "lesson_media_reviews",
    lesson_media_metadata,
    Column("review_id", String(64), primary_key=True),
    Column("media_id", String(64), ForeignKey("lesson_media_revisions.media_id", ondelete="CASCADE"), nullable=False),
    Column("media_version", Integer, nullable=False),
    Column("artifact_sha256", String(64), nullable=False),
    Column("reviewer", String(128), nullable=False),
    Column("actor_json", json_document, nullable=False),
    Column("decision", String(24), nullable=False),
    Column("note", Text, nullable=False, default=""),
    Column("created_at", Float, nullable=False),
    CheckConstraint("decision IN ('approve', 'request_changes', 'publish')", name="ck_lesson_media_review_decision"),
)
Index("ix_lesson_media_reviews_media_created", lesson_media_reviews.c.media_id, lesson_media_reviews.c.created_at)


class LessonMediaStore(DatabaseStore):
    """Persist immutable lesson video revisions and append-only human reviews."""

    @staticmethod
    def _row_to_revision(row: Any) -> dict[str, Any]:
        return {
            "mediaId": row["media_id"],
            "seriesId": row["series_id"],
            "version": int(row["version"]),
            "chapterId": row["chapter_id"],
            "publicationId": row["publication_id"],
            "lessonId": row["lesson_id"],
            "sourceRevisionId": row["source_revision_id"],
            "sourceFingerprint": row["source_fingerprint"],
            "title": row["title"],
            "subject": row["subject"],
            "license": row["license"],
            "sourceSnapshot": decode_json(row["source_snapshot_json"]),
            "scenes": decode_json(row["scenes_json"]),
            "rendererSnapshot": decode_json(row["renderer_snapshot_json"]),
            "status": row["status"],
            "distributionScope": row["distribution_scope"],
            "artifactPath": row["artifact_path"],
            "captionsPath": row["captions_path"],
            "artifactSha256": row["artifact_sha256"],
            "captionsSha256": row["captions_sha256"],
            "durationSeconds": row["duration_seconds"],
            "consistencyReview": decode_json(row["consistency_review_json"]) if row["consistency_review_json"] is not None else None,
            "publishedAt": row["published_at"],
            "createdBy": row["created_by"],
            "createdAt": row["created_at"],
        }

    def create_revision(self, revision: dict[str, Any]) -> dict[str, Any]:
        self._ensure_initialized()
        values = {
            "media_id": revision["mediaId"],
            "series_id": revision["seriesId"],
            "version": revision["version"],
            "chapter_id": revision["chapterId"],
            "publication_id": revision["publicationId"],
            "lesson_id": revision["lessonId"],
            "source_revision_id": revision["sourceRevisionId"],
            "source_fingerprint": revision["sourceFingerprint"],
            "title": revision["title"],
            "subject": revision["subject"],
            "license": revision["license"],
            "source_snapshot_json": revision["sourceSnapshot"],
            "scenes_json": revision["scenes"],
            "renderer_snapshot_json": revision["rendererSnapshot"],
            "status": revision["status"],
            "distribution_scope": revision["distributionScope"],
            "created_by": revision["createdBy"],
            "created_at": revision["createdAt"],
        }
        with self.engine.begin() as connection:
            connection.execute(lesson_media_revisions.insert().values(**values))
        return self.get_revision(revision["mediaId"])  # type: ignore[return-value]

    def get_revision(self, media_id: str) -> dict[str, Any] | None:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            row = connection.execute(select(lesson_media_revisions).where(lesson_media_revisions.c.media_id == media_id)).mappings().first()
        if not row:
            return None
        revision = self._row_to_revision(row)
        revision["reviews"] = self.list_reviews(media_id)
        return revision

    def get_series(self, series_id: str) -> list[dict[str, Any]]:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(lesson_media_revisions).where(lesson_media_revisions.c.series_id == series_id).order_by(lesson_media_revisions.c.version)
            ).mappings().all()
        return [self._row_to_revision(row) for row in rows]

    def list_for_lesson(self, chapter_id: str, publication_id: str, lesson_id: str) -> list[dict[str, Any]]:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(lesson_media_revisions).where(
                    lesson_media_revisions.c.chapter_id == chapter_id,
                    lesson_media_revisions.c.publication_id == publication_id,
                    lesson_media_revisions.c.lesson_id == lesson_id,
                ).order_by(lesson_media_revisions.c.created_at.desc())
            ).mappings().all()
        return [self._row_to_revision(row) for row in rows]

    def set_rendering(self, media_id: str, *, expected_version: int) -> bool:
        self._ensure_initialized()
        with self.engine.begin() as connection:
            result = connection.execute(
                update(lesson_media_revisions).where(
                    lesson_media_revisions.c.media_id == media_id,
                    lesson_media_revisions.c.version == expected_version,
                    lesson_media_revisions.c.status.in_(("draft", "consistency_failed", "changes_requested")),
                ).values(status="rendering")
            )
        return result.rowcount == 1

    def complete_render(self, media_id: str, *, expected_version: int, output: dict[str, Any]) -> bool:
        self._ensure_initialized()
        with self.engine.begin() as connection:
            result = connection.execute(
                update(lesson_media_revisions).where(
                    lesson_media_revisions.c.media_id == media_id,
                    lesson_media_revisions.c.version == expected_version,
                    lesson_media_revisions.c.status == "rendering",
                ).values(
                    status="consistency_review_required" if output["consistencyReview"]["passed"] else "consistency_failed",
                    artifact_path=output["artifactPath"],
                    captions_path=output["captionsPath"],
                    artifact_sha256=output["artifactSha256"],
                    captions_sha256=output["captionsSha256"],
                    duration_seconds=output["durationSeconds"],
                    consistency_review_json=output["consistencyReview"],
                    renderer_snapshot_json=output["rendererSnapshot"],
                )
            )
        return result.rowcount == 1

    def fail_render(self, media_id: str, *, expected_version: int) -> bool:
        self._ensure_initialized()
        with self.engine.begin() as connection:
            result = connection.execute(
                update(lesson_media_revisions).where(
                    lesson_media_revisions.c.media_id == media_id,
                    lesson_media_revisions.c.version == expected_version,
                    lesson_media_revisions.c.status == "rendering",
                ).values(status="consistency_failed")
            )
        return result.rowcount == 1

    def cancel_render(self, media_id: str, *, expected_version: int) -> bool:
        """Stop learner access immediately while the Worker reaches its cancel point."""
        self._ensure_initialized()
        with self.engine.begin() as connection:
            result = connection.execute(
                update(lesson_media_revisions).where(
                    lesson_media_revisions.c.media_id == media_id,
                    lesson_media_revisions.c.version == expected_version,
                    lesson_media_revisions.c.status == "rendering",
                ).values(
                    status="draft",
                    artifact_path=None,
                    captions_path=None,
                    artifact_sha256=None,
                    captions_sha256=None,
                    duration_seconds=None,
                    consistency_review_json=None,
                )
            )
        return result.rowcount == 1

    def discard_render(self, media_id: str, *, expected_version: int) -> bool:
        """Compensate a cancellation that raced the Worker success commit."""
        self._ensure_initialized()
        with self.engine.begin() as connection:
            result = connection.execute(
                update(lesson_media_revisions).where(
                    lesson_media_revisions.c.media_id == media_id,
                    lesson_media_revisions.c.version == expected_version,
                    lesson_media_revisions.c.status.in_(("rendering", "consistency_review_required", "consistency_failed")),
                ).values(
                    status="draft",
                    artifact_path=None,
                    captions_path=None,
                    artifact_sha256=None,
                    captions_sha256=None,
                    duration_seconds=None,
                    consistency_review_json=None,
                )
            )
        return result.rowcount == 1

    def add_review(self, review: dict[str, Any]) -> None:
        self._ensure_initialized()
        with self.engine.begin() as connection:
            connection.execute(lesson_media_reviews.insert().values(
                review_id=review["reviewId"],
                media_id=review["mediaId"],
                media_version=review["mediaVersion"],
                artifact_sha256=review["artifactSha256"],
                reviewer=review["reviewer"],
                actor_json=review["actor"],
                decision=review["decision"],
                note=review["note"],
                created_at=review["createdAt"],
            ))

    def add_review_and_transition(self, review: dict[str, Any], *, expected_status: str, new_status: str) -> bool:
        """Record the human decision and its version-bound state change atomically."""
        self._ensure_initialized()
        with self.engine.begin() as connection:
            updated = connection.execute(
                update(lesson_media_revisions).where(
                    lesson_media_revisions.c.media_id == review["mediaId"],
                    lesson_media_revisions.c.version == review["mediaVersion"],
                    lesson_media_revisions.c.status == expected_status,
                    lesson_media_revisions.c.artifact_sha256 == review["artifactSha256"],
                ).values(status=new_status)
            )
            if updated.rowcount != 1:
                return False
            connection.execute(lesson_media_reviews.insert().values(
                review_id=review["reviewId"],
                media_id=review["mediaId"],
                media_version=review["mediaVersion"],
                artifact_sha256=review["artifactSha256"],
                reviewer=review["reviewer"],
                actor_json=review["actor"],
                decision=review["decision"],
                note=review["note"],
                created_at=review["createdAt"],
            ))
        return True

    def publish_with_audit(self, review: dict[str, Any], *, published_at: float) -> bool:
        """Publish only the approved artifact and append the server-bound actor."""
        self._ensure_initialized()
        with self.engine.begin() as connection:
            updated = connection.execute(
                update(lesson_media_revisions).where(
                    lesson_media_revisions.c.media_id == review["mediaId"],
                    lesson_media_revisions.c.version == review["mediaVersion"],
                    lesson_media_revisions.c.status == "approved",
                    lesson_media_revisions.c.artifact_sha256 == review["artifactSha256"],
                ).values(status="published", published_at=published_at)
            )
            if updated.rowcount != 1:
                return False
            connection.execute(lesson_media_reviews.insert().values(
                review_id=review["reviewId"],
                media_id=review["mediaId"],
                media_version=review["mediaVersion"],
                artifact_sha256=review["artifactSha256"],
                reviewer=review["reviewer"],
                actor_json=review["actor"],
                decision="publish",
                note=review["note"],
                created_at=published_at,
            ))
        return True

    def list_reviews(self, media_id: str) -> list[dict[str, Any]]:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(lesson_media_reviews).where(lesson_media_reviews.c.media_id == media_id).order_by(lesson_media_reviews.c.created_at)
            ).mappings().all()
        return [{
            "reviewId": row["review_id"],
            "mediaId": row["media_id"],
            "mediaVersion": int(row["media_version"]),
            "artifactSha256": row["artifact_sha256"],
            "actor": decode_json(row["actor_json"]),
            "decision": row["decision"],
            "note": row["note"],
            "createdAt": row["created_at"],
        } for row in rows]

    def set_review_status(self, media_id: str, *, expected_status: str, new_status: str, published_at: float | None = None) -> bool:
        self._ensure_initialized()
        values: dict[str, Any] = {"status": new_status}
        if published_at is not None:
            values["published_at"] = published_at
        with self.engine.begin() as connection:
            result = connection.execute(
                update(lesson_media_revisions).where(
                    lesson_media_revisions.c.media_id == media_id,
                    lesson_media_revisions.c.status == expected_status,
                    lesson_media_revisions.c.artifact_sha256.is_not(None),
                ).values(**values)
            )
        return result.rowcount == 1
