"""Persistence operations for textbook uploads and generated questions."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, update

from persistence.base import DatabaseStore
from persistence.database import decode_json
from persistence.schema import (
    batch_questions,
    question_revisions,
    run_snapshots,
    upload_jobs,
)


class TextbookStore(DatabaseStore):
    """Store import jobs, question batches and recoverable library records."""

    def save_job(self, job: dict[str, Any]) -> None:
        self._ensure_initialized()
        result = job.get("result")
        import_id = result.get("importId") if isinstance(result, dict) else job.get("importId")
        now = time.time()
        values = {
            "upload_id": job["uploadId"],
            "import_id": import_id,
            "filename": job["filename"],
            "content_type": job["contentType"],
            "size": job["size"],
            "chunk_size": job["chunkSize"],
            "total_chunks": job["totalChunks"],
            "source_text": job.get("sourceText", ""),
            "directory": str(job["directory"]),
            "status": job["status"],
            "progress": job.get("progress", 0),
            "message": job.get("message", ""),
            "result_json": result,
            "started_at": job.get("startedAt", now),
            "updated_at": job.get("updatedAt", now),
            "completed_at": job.get("completedAt"),
        }
        with self.engine.begin() as connection:
            self._upsert(
                connection,
                upload_jobs,
                values,
                ["upload_id"],
                [
                    "import_id", "filename", "content_type", "size", "chunk_size",
                    "total_chunks", "source_text", "directory", "status", "progress",
                    "message", "result_json", "updated_at", "completed_at",
                ],
            )

    def load_job(self, upload_id: str) -> dict[str, Any] | None:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(upload_jobs).where(upload_jobs.c.upload_id == upload_id)
            ).mappings().first()
            if not row:
                return None
            question_rows = connection.execute(
                select(
                    batch_questions.c.batch_id,
                    batch_questions.c.payload_json,
                    batch_questions.c.guide_cards_json,
                )
                .where(batch_questions.c.upload_id == upload_id)
                .order_by(batch_questions.c.created_at, batch_questions.c.batch_id)
            ).mappings().all()
        result = decode_json(row["result_json"]) if row["result_json"] is not None else None
        return {
            "uploadId": row["upload_id"],
            "importId": row["import_id"],
            "filename": row["filename"],
            "contentType": row["content_type"],
            "size": row["size"],
            "chunkSize": row["chunk_size"],
            "totalChunks": row["total_chunks"],
            "sourceText": row["source_text"],
            "directory": Path(row["directory"]),
            "status": row["status"],
            "progress": row["progress"],
            "message": row["message"],
            "startedAt": row["started_at"],
            "updatedAt": row["updated_at"],
            "completedAt": row["completed_at"],
            "result": result,
            "batchPayloads": {
                item["batch_id"]: decode_json(item["payload_json"])
                for item in question_rows
            },
            "batchGuideCards": {
                item["batch_id"]: decode_json(item["guide_cards_json"])
                for item in question_rows
            },
            # The mapping is part of the persisted result, so a Worker restarted between
            # batches can still identify an already processed batch and skip it idempotently.
            "batchQuestionKeys": (result or {}).get("batchQuestionKeys", {}),
            "processingBatches": set(),
        }

    def save_question(
        self,
        upload_id: str,
        batch_id: str,
        payload: dict[str, Any],
        guide_cards: list[dict[str, Any]] | None = None,
    ) -> None:
        self.save_questions(upload_id, [(batch_id, payload, guide_cards or [])])

    def save_questions(
        self,
        upload_id: str,
        questions: list[tuple[str, dict[str, Any], list[dict[str, Any]]]],
    ) -> None:
        """Atomically insert or replace all generated questions in one call."""
        self._ensure_initialized()
        now = time.time()
        with self.engine.begin() as connection:
            for batch_id, payload, guide_cards in questions:
                self._upsert(
                    connection,
                    batch_questions,
                    {
                        "upload_id": upload_id,
                        "batch_id": batch_id,
                        "question_id": payload["question"]["id"],
                        "payload_json": payload,
                        "guide_cards_json": guide_cards,
                        "created_at": now,
                    },
                    ["upload_id", "batch_id"],
                    ["question_id", "payload_json", "guide_cards_json", "created_at"],
                )

    def list_imports(self) -> list[dict[str, Any]]:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(
                    upload_jobs.c.upload_id,
                    upload_jobs.c.import_id,
                    upload_jobs.c.filename,
                    upload_jobs.c.size,
                    upload_jobs.c.status,
                    upload_jobs.c.result_json,
                    upload_jobs.c.started_at,
                    upload_jobs.c.updated_at,
                )
                .where(
                    upload_jobs.c.status == "complete",
                    upload_jobs.c.result_json.is_not(None),
                )
                .order_by(upload_jobs.c.updated_at.desc())
            ).mappings().all()
        items = []
        for row in rows:
            result = decode_json(row["result_json"])
            extraction = result.get("extraction", {})
            items.append({
                "uploadId": row["upload_id"],
                "importId": row["import_id"],
                "filename": row["filename"],
                "size": row["size"],
                "status": row["status"],
                "questionCount": extraction.get("questionCount", 0),
                "pageCount": extraction.get("pageCount"),
                "chapter": extraction.get("chapter", "教材练习"),
                "updatedAt": row["updated_at"],
            })
        return items

    def find_completed_import(
        self,
        import_id: str,
        *,
        exclude_upload_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Return an existing completed import with the same content hash."""
        if not import_id:
            return None
        self._ensure_initialized()
        with self.engine.connect() as connection:
            condition = [
                upload_jobs.c.import_id == import_id,
                upload_jobs.c.status == "complete",
            ]
            if exclude_upload_id:
                condition.append(upload_jobs.c.upload_id != exclude_upload_id)
            row = connection.execute(
                select(upload_jobs.c.upload_id, upload_jobs.c.filename)
                .where(*condition)
                .order_by(upload_jobs.c.updated_at.asc())
                .limit(1)
            ).mappings().first()
        if not row:
            return None
        return {"uploadId": row["upload_id"], "filename": row["filename"]}

    def soft_delete_import(self, upload_id: str) -> bool:
        """Hide an import while retaining its generated data for recovery."""
        self._ensure_initialized()
        now = time.time()
        with self.engine.begin() as connection:
            result = connection.execute(
                upload_jobs.update()
                .where(
                    upload_jobs.c.upload_id == upload_id,
                    upload_jobs.c.status != "deleted",
                )
                .values(status="deleted", updated_at=now)
            )
        return result.rowcount > 0

    def create_run_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Insert a frozen running config; duplicate run IDs are rejected."""
        self._ensure_initialized()
        values = {
            "run_id": snapshot["runId"],
            "operation": snapshot["operation"],
            "scope": snapshot["scope"],
            "target_upload_id": snapshot.get("targetUploadId"),
            "target_question_key": snapshot.get("targetQuestionKey"),
            "target_publication_id": snapshot.get("targetPublicationId"),
            "status": "running",
            "config_json": snapshot.get("config") or {},
            "started_at": snapshot.get("startedAt", time.time()),
        }
        with self.engine.begin() as connection:
            connection.execute(run_snapshots.insert().values(**values))
        return self.get_run_snapshot(snapshot["runId"])  # type: ignore[return-value]

    def finish_run_snapshot(
        self,
        run_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Finalize exactly one running run; its config is never updated."""
        if status not in {"succeeded", "failed"}:
            raise ValueError("运行只能从 running 结束为 succeeded 或 failed")
        self._ensure_initialized()
        with self.engine.begin() as connection:
            changed = connection.execute(
                update(run_snapshots)
                .where(run_snapshots.c.run_id == run_id, run_snapshots.c.status == "running")
                .values(status=status, result_json=result, error_json=error, completed_at=time.time())
            )
        if changed.rowcount != 1:
            raise ValueError("运行不存在，或已经结束；不可再次修改运行快照")
        return self.get_run_snapshot(run_id)  # type: ignore[return-value]

    def get_run_snapshot(self, run_id: str) -> dict[str, Any] | None:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(run_snapshots).where(run_snapshots.c.run_id == run_id)
            ).mappings().first()
        if not row:
            return None
        return {
            "runId": row["run_id"], "operation": row["operation"], "scope": row["scope"],
            "targetUploadId": row["target_upload_id"], "targetQuestionKey": row["target_question_key"],
            "targetPublicationId": row["target_publication_id"], "status": row["status"],
            "config": decode_json(row["config_json"]), "result": decode_json(row["result_json"]) if row["result_json"] is not None else None,
            "error": decode_json(row["error_json"]) if row["error_json"] is not None else None,
            "startedAt": row["started_at"], "completedAt": row["completed_at"],
        }

    def list_run_snapshots(self, *, upload_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        self._ensure_initialized()
        query = select(run_snapshots).order_by(run_snapshots.c.started_at.desc()).limit(min(limit, 200))
        if upload_id:
            query = query.where(run_snapshots.c.target_upload_id == upload_id)
        with self.engine.connect() as connection:
            ids = [row["run_id"] for row in connection.execute(query).mappings().all()]
        snapshots = [self.get_run_snapshot(run_id) for run_id in ids]
        return [snapshot for snapshot in snapshots if snapshot is not None]

    def append_question_revision(
        self,
        *,
        upload_id: str,
        source_question_key: str,
        operation: str,
        payload: dict[str, Any],
        guide_cards: list[dict[str, Any]],
        run_id: str,
    ) -> dict[str, Any]:
        """Append a revision and never update an existing payload."""
        self._ensure_initialized()
        revision_id = uuid.uuid4().hex
        with self.engine.begin() as connection:
            previous = connection.execute(
                select(question_revisions.c.revision_id, question_revisions.c.revision_number)
                .where(
                    question_revisions.c.upload_id == upload_id,
                    question_revisions.c.source_question_key == source_question_key,
                )
                .order_by(question_revisions.c.revision_number.desc())
                .limit(1)
            ).mappings().first()
            number = int(previous["revision_number"] + 1) if previous else 1
            created_at = time.time()
            connection.execute(question_revisions.insert().values(
                revision_id=revision_id, upload_id=upload_id, source_question_key=source_question_key,
                revision_number=number, operation=operation,
                previous_revision_id=previous["revision_id"] if previous else None,
                payload_json=payload, guide_cards_json=guide_cards, run_id=run_id, created_at=created_at,
            ))
        return {
            "revisionId": revision_id, "uploadId": upload_id, "sourceQuestionKey": source_question_key,
            "revisionNumber": number, "operation": operation,
            "previousRevisionId": previous["revision_id"] if previous else None,
            "runId": run_id, "createdAt": created_at,
        }

    def append_revision_and_save_question(
        self,
        *,
        upload_id: str,
        source_question_key: str,
        operation: str,
        payload: dict[str, Any],
        guide_cards: list[dict[str, Any]],
        run_id: str,
    ) -> dict[str, Any]:
        """Single-question convenience wrapper around the batch transaction."""
        return self.append_revisions_and_save_questions(
            upload_id=upload_id,
            questions=[(source_question_key, payload, guide_cards)],
            operation=operation,
            run_id=run_id,
        )[0]

    def append_revisions_and_save_questions(
        self,
        *,
        upload_id: str,
        questions: list[tuple[str, dict[str, Any], list[dict[str, Any]]]],
        operation: str,
        run_id: str,
        replace_keys: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Atomically append a whole batch and update its latest question views.

        ``lesson_documents`` is intentionally written by the caller after this
        transaction; the student publication boundary never reads the mutable
        ``batch_questions`` view directly. Every revision and current-question
        upsert in this batch shares one transaction, so a failure in any row
        leaves all previous successful payloads and revision chains unchanged.
        """
        self._ensure_initialized()
        if not questions:
            return []
        # Validate required materialized fields before opening the transaction;
        # database errors inside the transaction are rolled back by begin().
        for _source_question_key, payload, _guide_cards in questions:
            payload["question"]["id"]
        revisions: list[dict[str, Any]] = []
        with self.engine.begin() as connection:
            if replace_keys:
                # 整批重生成必须移除本批次不再出现的旧题；否则内存看似已替换，进程
                # 重启后却会从 batch_questions 重新加载“幽灵题目”。revision 历史保留。
                connection.execute(
                    delete(batch_questions).where(
                        batch_questions.c.upload_id == upload_id,
                        batch_questions.c.batch_id.in_(replace_keys),
                    )
                )
            for source_question_key, payload, guide_cards in questions:
                revision_id = uuid.uuid4().hex
                created_at = time.time()
                previous = connection.execute(
                    select(question_revisions.c.revision_id, question_revisions.c.revision_number)
                    .where(
                        question_revisions.c.upload_id == upload_id,
                        question_revisions.c.source_question_key == source_question_key,
                    )
                    .order_by(question_revisions.c.revision_number.desc())
                    .limit(1)
                ).mappings().first()
                number = int(previous["revision_number"] + 1) if previous else 1
                connection.execute(question_revisions.insert().values(
                    revision_id=revision_id, upload_id=upload_id, source_question_key=source_question_key,
                    revision_number=number, operation=operation,
                    previous_revision_id=previous["revision_id"] if previous else None,
                    payload_json=payload, guide_cards_json=guide_cards, run_id=run_id, created_at=created_at,
                ))
                self._upsert(
                    connection,
                    batch_questions,
                    {
                        "upload_id": upload_id,
                        "batch_id": source_question_key,
                        "question_id": payload["question"]["id"],
                        "payload_json": payload,
                        "guide_cards_json": guide_cards,
                        "created_at": created_at,
                    },
                    ["upload_id", "batch_id"],
                    ["question_id", "payload_json", "guide_cards_json", "created_at"],
                )
                revisions.append({
                    "revisionId": revision_id, "uploadId": upload_id,
                    "sourceQuestionKey": source_question_key, "revisionNumber": number,
                    "operation": operation,
                    "previousRevisionId": previous["revision_id"] if previous else None,
                    "runId": run_id, "createdAt": created_at,
                })
        return revisions

    def list_question_revisions(self, upload_id: str, source_question_key: str) -> list[dict[str, Any]]:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(question_revisions)
                .where(question_revisions.c.upload_id == upload_id, question_revisions.c.source_question_key == source_question_key)
                .order_by(question_revisions.c.revision_number)
            ).mappings().all()
        return [{
            "revisionId": row["revision_id"], "uploadId": row["upload_id"], "sourceQuestionKey": row["source_question_key"],
            "revisionNumber": row["revision_number"], "operation": row["operation"],
            "previousRevisionId": row["previous_revision_id"], "payload": decode_json(row["payload_json"]),
            "guideCards": decode_json(row["guide_cards_json"]), "runId": row["run_id"], "createdAt": row["created_at"],
        } for row in rows]
