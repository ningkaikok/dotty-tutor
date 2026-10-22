"""Transactional persistence for courseware drafts, immutable publications, and attempts."""

from __future__ import annotations

import copy
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

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
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import Connection, Engine

from domain.courseware.batches import active_plan_versions
from persistence.database import decode_json
from persistence.schema import json_document

courseware_metadata = MetaData()

courseware_records = Table(
    "courseware_records",
    courseware_metadata,
    Column("record_id", String(128), primary_key=True),
    Column("record_type", String(16), nullable=False),
    Column("chapter_id", String(64), nullable=False),
    Column("courseware_id", String(64)),
    Column("source_revision_id", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("record_json", json_document, nullable=False),
    Column("record_version", Integer, nullable=False, default=1),
    Column("deleted_at", Float),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    CheckConstraint("record_type IN ('plan', 'document')", name="ck_courseware_records_type"),
    CheckConstraint("record_version > 0", name="ck_courseware_records_version"),
    UniqueConstraint("courseware_id", name="uq_courseware_records_courseware_id"),
    Index("ix_courseware_records_chapter_type", "chapter_id", "record_type"),
    Index("ix_courseware_records_courseware", "courseware_id"),
)

courseware_publications = Table(
    "courseware_publications",
    courseware_metadata,
    Column("publication_id", String(64), primary_key=True),
    Column("courseware_id", String(64), ForeignKey("courseware_records.courseware_id", ondelete="RESTRICT"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("source_revision_id", String(64), nullable=False),
    Column("source_title", String(200)),
    Column("source_url", String(500)),
    Column("attribution_text", String(500)),
    Column("distribution_scope", String(16), nullable=False),
    Column("snapshot_json", json_document, nullable=False),
    Column("review_records_json", json_document, nullable=False, default=list),
    Column("published_at", Float, nullable=False),
    UniqueConstraint("courseware_id", "version", name="uq_courseware_publication_version"),
    CheckConstraint("version > 0", name="ck_courseware_publications_version"),
    CheckConstraint("distribution_scope IN ('private', 'classroom', 'public')", name="ck_courseware_publications_scope"),
)

courseware_attempts = Table(
    "courseware_attempts",
    courseware_metadata,
    Column("attempt_id", String(64), primary_key=True),
    Column("publication_id", String(64), ForeignKey("courseware_publications.publication_id", ondelete="RESTRICT"), nullable=False),
    Column("courseware_id", String(64), nullable=False),
    Column("learner_id", String(128), nullable=False),
    Column("activity_id", String(128), nullable=False),
    Column("command_id", String(128), nullable=False),
    Column("response_json", json_document, nullable=False),
    Column("assessment", String(24), nullable=False),
    Column("feedback", Text, nullable=False),
    Column("created_at", Float, nullable=False),
    UniqueConstraint("publication_id", "learner_id", "command_id", name="uq_courseware_attempt_command"),
    CheckConstraint("assessment IN ('correct', 'incorrect', 'needs_review', 'participation')", name="ck_courseware_attempt_assessment"),
    Index("ix_courseware_attempts_learner", "learner_id", "created_at"),
)


class CoursewareStaleExecution(ValueError):
    """A worker lost its claim or the source/document changed before checkpoint."""


class CoursewareStore:
    """Use the application's existing engine and transaction for all courseware state."""

    def __init__(self, *, engine: Engine) -> None:
        self.engine = engine
        self._active_connection: ContextVar[Connection | None] = ContextVar(
            f"courseware_connection_{id(self)}", default=None,
        )

    def with_automation_status(self, document: dict[str, Any]) -> dict[str, Any]:
        """Project terminal jobs as exceptions, including lease loss and cancellation.

        Do not rewrite the document: retrying the same failed job must retain its
        frozen input version and resume from its exact-input checkpoints.
        """
        state = document.get("automation")
        if not state or state.get("status") not in {"generating", "checking", "repairing"}:
            return document
        job_id = state.get("jobId") if state["status"] == "checking" else document.get("generationJobId")
        if not job_id:
            return document
        from persistence.schema import background_jobs

        with self.read_connection() as connection:
            row = connection.execute(select(background_jobs).where(background_jobs.c.job_id == job_id)).mappings().first()
        if row and row["status"] in {"failed", "cancelled"}:
            result = copy.deepcopy(document)
            error = decode_json(row["last_error_json"]) or {}
            result["automation"].update(status="needs_attention", jobId=job_id,
                message=str(error.get("message") or "自动制作已取消，可重新制作或手动编辑。")[:1000],
                retryable=row["status"] == "failed")
            return result
        return document

    @contextmanager
    def atomic(self) -> Iterator[Connection]:
        active = self._active_connection.get()
        if active is not None:
            yield active
            return
        with self.engine.begin() as connection:
            token = self._active_connection.set(connection)
            try:
                yield connection
            finally:
                self._active_connection.reset(token)

    @contextmanager
    def read_connection(self) -> Iterator[Connection]:
        active = self._active_connection.get()
        if active is not None:
            yield active
        else:
            with self.engine.connect() as connection:
                yield connection

    @staticmethod
    def _record(row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        payload = decode_json(row["record_json"])
        payload["recordVersion"] = int(row["record_version"])
        if row["deleted_at"] is not None:
            payload["deletedAt"] = row["deleted_at"]
        return payload

    def load_record(self, record_id: str, *, lock: bool = False) -> dict[str, Any] | None:
        with self.read_connection() as connection:
            query = select(courseware_records).where(courseware_records.c.record_id == record_id)
            if lock:
                query = query.with_for_update()
            row = connection.execute(query).mappings().first()
        return self._record(row)

    def load_plan(self, chapter_id: str, *, lock: bool = False) -> dict[str, Any] | None:
        return self.load_record(f"plan:{chapter_id}", lock=lock)

    def load_document(self, courseware_id: str, *, lock: bool = False) -> dict[str, Any] | None:
        return self.load_record(f"document:{courseware_id}", lock=lock)

    def list_documents(self, chapter_id: str, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        with self.read_connection() as connection:
            query = select(courseware_records).where(
                courseware_records.c.chapter_id == chapter_id,
                courseware_records.c.record_type == "document",
            ).order_by(courseware_records.c.created_at, courseware_records.c.record_id)
            if not include_deleted:
                query = query.where(courseware_records.c.deleted_at.is_(None))
            rows = connection.execute(query).mappings().all()
        records: list[dict[str, Any]] = []
        for row in rows:
            record = self._record(row)
            if record is not None:
                records.append(record)
        return records

    def save_record(
        self,
        *,
        record_id: str,
        record_type: str,
        chapter_id: str,
        courseware_id: str | None,
        source_revision_id: str,
        payload: dict[str, Any],
        status: str,
        expected_version: int | None,
        deleted_at: float | None = None,
        connection: Connection | None = None,
    ) -> dict[str, Any]:
        timestamp = time.time()
        target = connection or self._active_connection.get()
        if target is None:
            with self.atomic() as active:
                return self.save_record(
                    record_id=record_id, record_type=record_type, chapter_id=chapter_id,
                    courseware_id=courseware_id, source_revision_id=source_revision_id,
                    payload=payload, status=status, expected_version=expected_version,
                    deleted_at=deleted_at, connection=active,
                )
        row = target.execute(
            select(courseware_records.c.record_version).where(
                courseware_records.c.record_id == record_id,
            ).with_for_update()
        ).scalar_one_or_none()
        if row is None:
            if expected_version not in (None, 0):
                raise ValueError("课件记录已变化，请刷新后重试")
            version = 1
            payload["recordVersion"] = version
            target.execute(courseware_records.insert().values(
                record_id=record_id, record_type=record_type, chapter_id=chapter_id,
                courseware_id=courseware_id, source_revision_id=source_revision_id,
                status=status, record_json=payload, record_version=version,
                deleted_at=deleted_at, created_at=timestamp, updated_at=timestamp,
            ))
            return payload
        if expected_version is None or int(row) != expected_version:
            raise ValueError("课件记录已被其他编辑更新，请刷新后重试")
        version = int(row) + 1
        payload["recordVersion"] = version
        target.execute(courseware_records.update().where(
            courseware_records.c.record_id == record_id,
            courseware_records.c.record_version == expected_version,
        ).values(
            record_type=record_type, chapter_id=chapter_id, courseware_id=courseware_id,
            source_revision_id=source_revision_id, status=status, record_json=payload,
            record_version=version, deleted_at=deleted_at, updated_at=timestamp,
        ))
        return payload

    def load_chapter(self, chapter_id: str, *, lock: bool = False) -> dict[str, Any] | None:
        from persistence.schema import chapter_records

        with self.read_connection() as connection:
            query = select(chapter_records).where(chapter_records.c.chapter_id == chapter_id)
            if lock:
                query = query.with_for_update()
            row = connection.execute(query).mappings().first()
        if row is None:
            return None
        chapter = decode_json(row["chapter_json"])
        chapter["recordVersion"] = int(row["record_version"])
        return chapter

    def checkpoint_is_current(
        self,
        connection: Connection,
        *,
        context: Any,
        chapter_id: str,
        chapter_version: int,
        source_revision_id: str,
        courseware_id: str,
        document_version: int,
        page_id: str,
        page_version: int,
        plan_version: int,
        now: float,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        from persistence.schema import background_jobs, chapter_records

        job = connection.execute(select(background_jobs).where(
            background_jobs.c.job_id == context.job_id,
            background_jobs.c.status == "running",
            background_jobs.c.lease_owner == context.worker_id,
            background_jobs.c.attempt_count == context.attempt_count,
            background_jobs.c.lease_expires_at > now,
            background_jobs.c.cancel_requested.is_(False),
        ).with_for_update()).mappings().first()
        if job is None:
            raise CoursewareStaleExecution("课件生成租约已过期或已被接管")
        chapter_row = connection.execute(select(chapter_records).where(
            chapter_records.c.chapter_id == chapter_id,
        ).with_for_update()).mappings().first()
        if chapter_row is None:
            raise CoursewareStaleExecution("教材章节不存在")
        chapter = decode_json(chapter_row["chapter_json"])
        if chapter.get("deletedAt") or int(chapter_row["record_version"]) != chapter_version:
            raise CoursewareStaleExecution("教材章节已删除或发生修改，旧生成结果未写入")
        revisions = chapter.get("sourceRevisions", [])
        if not revisions or revisions[-1].get("sourceRevisionId") != source_revision_id:
            raise CoursewareStaleExecution("教材来源修订已变化，旧生成结果未写入")
        upload_id = revisions[-1].get("uploadId")
        if upload_id:
            from persistence.schema import upload_jobs

            upload_status = connection.execute(select(upload_jobs.c.status).where(
                upload_jobs.c.upload_id == upload_id,
            ).with_for_update()).scalar_one_or_none()
            if upload_status in (None, "deleted"):
                raise CoursewareStaleExecution("教材来源已删除，旧生成结果未写入")
        plan = connection.execute(select(courseware_records).where(
            courseware_records.c.record_id == f"plan:{chapter_id}",
        ).with_for_update()).mappings().first()
        if plan is None or plan["deleted_at"] is not None:
            raise CoursewareStaleExecution("课件规划不存在或已删除")
        plan_payload = decode_json(plan["record_json"])
        if plan_version not in active_plan_versions(plan_payload, source_revision_id):
            raise CoursewareStaleExecution("课件规划已变化，旧页面生成结果未写入")
        record = connection.execute(select(courseware_records).where(
            courseware_records.c.record_id == f"document:{courseware_id}",
        ).with_for_update()).mappings().first()
        if record is None or record["deleted_at"] is not None:
            raise CoursewareStaleExecution("课件已删除，旧生成结果未写入")
        document = decode_json(record["record_json"])
        if int(record["record_version"]) != document_version or document.get("sourceRevisionId") != source_revision_id:
            raise CoursewareStaleExecution("课件版本或来源已变化，旧生成结果未写入")
        if document.get("generationJobId") != context.job_id:
            raise CoursewareStaleExecution("课件已绑定其他生成任务，旧结果未写入")
        page = next((item for item in document.get("pages", []) if item.get("pageId") == page_id), None)
        if page is None or int(page.get("version", 0)) != page_version:
            raise CoursewareStaleExecution("目标页面版本已变化，旧生成结果未写入")
        return chapter, document

    def checkpoint_plan(
        self,
        *,
        context: Any,
        chapter_id: str,
        chapter_version: int,
        source_revision_id: str,
        expected_plan_record_version: int,
        plan: dict[str, Any],
        now: float,
    ) -> dict[str, Any]:
        """Commit a generated plan only while its exact claim and source remain current."""
        from persistence.schema import background_jobs, chapter_records, upload_jobs

        with self.atomic() as connection:
            job = connection.execute(select(background_jobs).where(
                background_jobs.c.job_id == context.job_id,
                background_jobs.c.status == "running",
                background_jobs.c.lease_owner == context.worker_id,
                background_jobs.c.attempt_count == context.attempt_count,
                background_jobs.c.lease_expires_at > now,
                background_jobs.c.cancel_requested.is_(False),
            ).with_for_update()).first()
            if job is None:
                raise CoursewareStaleExecution("规划任务租约已过期或已被接管")
            chapter_row = connection.execute(select(chapter_records).where(
                chapter_records.c.chapter_id == chapter_id,
            ).with_for_update()).mappings().first()
            if chapter_row is None:
                raise CoursewareStaleExecution("教材章节不存在")
            chapter = decode_json(chapter_row["chapter_json"])
            revisions = chapter.get("sourceRevisions", [])
            if chapter.get("deletedAt") or int(chapter_row["record_version"]) != chapter_version:
                raise CoursewareStaleExecution("章节已删除或发生变化，规划结果未写入")
            if not revisions or revisions[-1].get("sourceRevisionId") != source_revision_id:
                raise CoursewareStaleExecution("来源修订已变化，规划结果未写入")
            upload_id = revisions[-1].get("uploadId")
            if upload_id:
                status = connection.execute(select(upload_jobs.c.status).where(
                    upload_jobs.c.upload_id == upload_id,
                ).with_for_update()).scalar_one_or_none()
                if status in (None, "deleted"):
                    raise CoursewareStaleExecution("教材来源已删除，规划结果未写入")
            current = connection.execute(select(courseware_records).where(
                courseware_records.c.record_id == f"plan:{chapter_id}",
            ).with_for_update()).mappings().first()
            if current is None or current["deleted_at"] is not None:
                raise CoursewareStaleExecution("规划记录不存在或已删除")
            payload = decode_json(current["record_json"])
            if int(current["record_version"]) != expected_plan_record_version:
                raise CoursewareStaleExecution("规划已编辑，生成结果未写入")
            if payload.get("generationJobId") != context.job_id:
                raise CoursewareStaleExecution("规划已绑定其他生成任务，旧结果未写入")
            plan["recordVersion"] = expected_plan_record_version
            plan["generationStatus"] = "completed"
            plan["updatedAt"] = now
            plan["planVersion"] = int(payload.get("planVersion", 1))
            return self.save_record(
                record_id=f"plan:{chapter_id}", record_type="plan", chapter_id=chapter_id,
                courseware_id=None, source_revision_id=source_revision_id,
                payload=plan, status=plan["status"], expected_version=expected_plan_record_version,
                connection=connection,
            )

    def save_generated_page(
        self,
        *,
        context: Any,
        chapter_id: str,
        chapter_version: int,
        source_revision_id: str,
        plan_version: int,
        courseware_id: str,
        expected_document_version: int,
        page_id: str,
        page_version: int,
        generated_page: dict[str, Any],
        progress: int,
        now: float,
    ) -> dict[str, Any]:
        """Save one page and its worker progress in a single fenced transaction."""
        from persistence.schema import background_jobs

        with self.atomic() as connection:
            chapter, document = self.checkpoint_is_current(
                connection, context=context, chapter_id=chapter_id,
                chapter_version=chapter_version, source_revision_id=source_revision_id,
                courseware_id=courseware_id, document_version=expected_document_version,
                page_id=page_id, page_version=page_version, plan_version=plan_version, now=now,
            )
            for index, item in enumerate(document["pages"]):
                if item["pageId"] == page_id:
                    generated_page["pageId"] = page_id
                    generated_page["sectionId"] = item["sectionId"]
                    generated_page["version"] = page_version + 1
                    generated_page["generationStatus"] = "completed"
                    generated_page["reviewStatus"] = "pending"
                    generated_page["error"] = None
                    document["pages"][index] = generated_page
                    break
            else:  # Protected by checkpoint_is_current, kept explicit for corrupt JSON.
                raise CoursewareStaleExecution("目标页不在课件草稿中")
            document["generationStatus"] = "running"
            document["updatedAt"] = now
            saved = self.save_record(
                record_id=f"document:{courseware_id}", record_type="document",
                chapter_id=chapter_id, courseware_id=courseware_id,
                source_revision_id=source_revision_id, payload=document,
                status=document["status"], expected_version=expected_document_version,
                connection=connection,
            )
            changed = connection.execute(update(background_jobs).where(
                background_jobs.c.job_id == context.job_id,
                background_jobs.c.status == "running",
                background_jobs.c.lease_owner == context.worker_id,
                background_jobs.c.attempt_count == context.attempt_count,
                background_jobs.c.lease_expires_at > now,
                background_jobs.c.cancel_requested.is_(False),
            ).values(progress=max(10, min(progress, 95)), message=f"已完成页面 {page_id}"[:1000], updated_at=now))
            if changed.rowcount != 1:
                raise CoursewareStaleExecution("页面检查点提交时任务租约已变化")
            return saved

    def finish_generation(
        self,
        *,
        context: Any,
        chapter_id: str,
        chapter_version: int,
        source_revision_id: str,
        plan_version: int,
        courseware_id: str,
        expected_document_version: int,
        now: float,
    ) -> dict[str, Any]:
        """Commit the final status only while the same leased source is current."""
        from persistence.schema import background_jobs, chapter_records

        with self.atomic() as connection:
            job = connection.execute(select(background_jobs.c.job_id).where(
                background_jobs.c.job_id == context.job_id,
                background_jobs.c.status == "running",
                background_jobs.c.lease_owner == context.worker_id,
                background_jobs.c.attempt_count == context.attempt_count,
                background_jobs.c.lease_expires_at > now,
                background_jobs.c.cancel_requested.is_(False),
            ).with_for_update()).scalar_one_or_none()
            if job is None:
                raise CoursewareStaleExecution("课件生成任务租约已过期或已被接管")
            chapter_row = connection.execute(select(chapter_records).where(
                chapter_records.c.chapter_id == chapter_id,
            ).with_for_update()).mappings().first()
            if chapter_row is None:
                raise CoursewareStaleExecution("教材章节不存在")
            chapter = decode_json(chapter_row["chapter_json"])
            revisions = chapter.get("sourceRevisions", [])
            if (chapter.get("deletedAt") or int(chapter_row["record_version"]) != chapter_version
                    or not revisions or revisions[-1].get("sourceRevisionId") != source_revision_id):
                raise CoursewareStaleExecution("教材章节或来源已变化，课件结果未写入")
            plan_row = connection.execute(select(courseware_records).where(
                courseware_records.c.record_id == f"plan:{chapter_id}",
            ).with_for_update()).mappings().first()
            if plan_row is None or plan_version not in active_plan_versions(
                decode_json(plan_row["record_json"]), source_revision_id,
            ):
                raise CoursewareStaleExecution("课件规划已变化，课件结果未写入")
            row = connection.execute(select(courseware_records).where(
                courseware_records.c.record_id == f"document:{courseware_id}",
            ).with_for_update()).mappings().first()
            if row is None or row["deleted_at"] is not None or int(row["record_version"]) != expected_document_version:
                raise CoursewareStaleExecution("课件草稿已变化，课件结果未写入")
            document = decode_json(row["record_json"])
            if document.get("sourceRevisionId") != source_revision_id or document.get("planVersion") != plan_version:
                raise CoursewareStaleExecution("课件规划或来源已变化，课件结果未写入")
            if document.get("generationJobId") != context.job_id:
                raise CoursewareStaleExecution("课件已绑定其他生成任务，课件结果未写入")
            if not document.get("pages") or any(page.get("generationStatus") != "completed" for page in document["pages"]):
                raise CoursewareStaleExecution("仍有课件页面尚未完成生成")
            document["generationStatus"] = "completed"
            document["reviewIssues"] = [
                issue for issue in document.get("reviewIssues", [])
                if issue.get("code") != "generation_failed"
            ]
            document["updatedAt"] = now
            return self.save_record(
                record_id=f"document:{courseware_id}", record_type="document",
                chapter_id=chapter_id, courseware_id=courseware_id,
                source_revision_id=source_revision_id, payload=document,
                status=document["status"], expected_version=expected_document_version,
                connection=connection,
            )

    def mark_generation_failed(
        self,
        *,
        context: Any,
        chapter_id: str,
        chapter_version: int,
        source_revision_id: str,
        plan_version: int,
        courseware_id: str,
        expected_document_version: int,
        error: str,
        now: float,
        failed_page_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Record a retryable draft error only if this exact attempt still owns it."""
        from persistence.schema import background_jobs, chapter_records

        with self.atomic() as connection:
            job = connection.execute(select(background_jobs.c.job_id).where(
                background_jobs.c.job_id == context.job_id,
                background_jobs.c.status == "running",
                background_jobs.c.lease_owner == context.worker_id,
                background_jobs.c.attempt_count == context.attempt_count,
                background_jobs.c.lease_expires_at > now,
                background_jobs.c.cancel_requested.is_(False),
            ).with_for_update()).scalar_one_or_none()
            if job is None:
                return None
            chapter_row = connection.execute(select(chapter_records).where(
                chapter_records.c.chapter_id == chapter_id,
            ).with_for_update()).mappings().first()
            if chapter_row is None:
                return None
            chapter = decode_json(chapter_row["chapter_json"])
            revisions = chapter.get("sourceRevisions", [])
            if (chapter.get("deletedAt") or int(chapter_row["record_version"]) != chapter_version
                    or not revisions or revisions[-1].get("sourceRevisionId") != source_revision_id):
                return None
            plan_row = connection.execute(select(courseware_records.c.record_json).where(
                courseware_records.c.record_id == f"plan:{chapter_id}",
            ).with_for_update()).scalar_one_or_none()
            if plan_row is None or plan_version not in active_plan_versions(decode_json(plan_row), source_revision_id):
                return None
            row = connection.execute(select(courseware_records).where(
                courseware_records.c.record_id == f"document:{courseware_id}",
            ).with_for_update()).mappings().first()
            if row is None or row["deleted_at"] is not None or int(row["record_version"]) != expected_document_version:
                return None
            document = decode_json(row["record_json"])
            if document.get("generationJobId") != context.job_id:
                return None
            if failed_page_id:
                failed_page = next((item for item in document.get("pages", [])
                                    if item.get("pageId") == failed_page_id), None)
                if failed_page and failed_page.get("generationStatus") != "completed":
                    failed_page["generationStatus"] = "failed"
                    failed_page["error"] = error[:1000]
            document["generationStatus"] = "partial" if any(
                page.get("generationStatus") == "completed" for page in document.get("pages", [])
            ) else "failed"
            document["reviewIssues"] = [
                *document.get("reviewIssues", []),
                {"code": "generation_failed", "message": error[:1000]},
            ][-200:]
            document["updatedAt"] = now
            return self.save_record(
                record_id=f"document:{courseware_id}", record_type="document",
                chapter_id=chapter_id, courseware_id=courseware_id,
                source_revision_id=source_revision_id, payload=document,
                status=document["status"], expected_version=expected_document_version,
                connection=connection,
            )

    def save_publication(self, value: dict[str, Any], *, connection: Connection | None = None) -> None:
        target = connection or self._active_connection.get()
        if target is None:
            with self.atomic() as active:
                self.save_publication(value, connection=active)
                return
        target.execute(courseware_publications.insert().values(
            publication_id=value["publicationId"], courseware_id=value["coursewareId"],
            version=value["version"], source_revision_id=value["sourceRevisionId"],
            source_title=value.get("sourceTitle"), source_url=value.get("sourceUrl"),
            attribution_text=value.get("attributionText"),
            distribution_scope=value["distributionScope"], snapshot_json=value["snapshot"],
            review_records_json=value["reviewRecords"], published_at=value["publishedAt"],
        ))

    @staticmethod
    def _publication(row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "publicationId": row["publication_id"], "coursewareId": row["courseware_id"],
            "version": row["version"], "sourceRevisionId": row["source_revision_id"],
            "sourceTitle": row["source_title"], "sourceUrl": row["source_url"],
            "attributionText": row["attribution_text"],
            "distributionScope": row["distribution_scope"],
            "snapshot": decode_json(row["snapshot_json"]),
            "reviewRecords": decode_json(row["review_records_json"]),
            "publishedAt": row["published_at"],
        }

    def load_publication(self, publication_id: str) -> dict[str, Any] | None:
        with self.read_connection() as connection:
            row = connection.execute(select(courseware_publications).where(
                courseware_publications.c.publication_id == publication_id,
            )).mappings().first()
        return self._publication(row)

    def latest_publication(self, courseware_id: str) -> dict[str, Any] | None:
        with self.read_connection() as connection:
            row = connection.execute(select(courseware_publications).where(
                courseware_publications.c.courseware_id == courseware_id,
            ).order_by(courseware_publications.c.version.desc()).limit(1)).mappings().first()
        return self._publication(row)

    def save_attempt(self, value: dict[str, Any], *, connection: Connection | None = None) -> dict[str, Any]:
        target = connection or self._active_connection.get()
        if target is None:
            with self.atomic() as active:
                return self.save_attempt(value, connection=active)
        statement = postgresql_insert(courseware_attempts).values(
            attempt_id=value["attemptId"], publication_id=value["publicationId"],
            courseware_id=value["coursewareId"], learner_id=value["learnerId"],
            activity_id=value["activityId"], command_id=value["commandId"],
            response_json=value["response"], assessment=value["assessment"],
            feedback=value["feedback"], created_at=value["createdAt"],
        ).on_conflict_do_nothing(constraint="uq_courseware_attempt_command")
        target.execute(statement)
        existing = target.execute(select(courseware_attempts).where(
            courseware_attempts.c.publication_id == value["publicationId"],
            courseware_attempts.c.learner_id == value["learnerId"],
            courseware_attempts.c.command_id == value["commandId"],
        )).mappings().one()
        saved = self._attempt(existing)
        if any(saved.get(key) != value.get(key) for key in ("activityId", "response", "assessment", "feedback")):
            raise ValueError("commandId 已用于不同活动或答案")
        return saved

    @staticmethod
    def _attempt(row: Any) -> dict[str, Any]:
        return {
            "attemptId": row["attempt_id"], "publicationId": row["publication_id"],
            "coursewareId": row["courseware_id"], "learnerId": row["learner_id"],
            "activityId": row["activity_id"], "commandId": row["command_id"],
            "response": decode_json(row["response_json"]), "assessment": row["assessment"],
            "feedback": row["feedback"], "createdAt": row["created_at"],
        }
