"""Persistence operations for textbook uploads and generated questions."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from sqlalchemy import select

from persistence.base import DatabaseStore
from persistence.database import decode_json
from persistence.schema import batch_questions, upload_jobs


class TextbookStore(DatabaseStore):
    """Store import jobs, question batches and recoverable library records."""

    def _resolve_directory(self, stored_directory: str) -> Path:
        """Resolve paths persisted before the standalone project was moved."""
        candidate = Path(stored_directory).expanduser()
        if candidate.exists():
            return candidate
        parts = candidate.parts
        if "tutor-demo" in parts:
            marker = max(index for index, part in enumerate(parts) if part == "tutor-demo")
            migrated = self.root.parent.joinpath(*parts[marker + 1:])
            if migrated.exists():
                return migrated
        return candidate

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
            "directory": self._resolve_directory(row["directory"]),
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
