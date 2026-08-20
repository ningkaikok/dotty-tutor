"""Background task handlers for textbook processing.

Handlers deliberately delegate to :class:`TextbookProcessingService`; the worker
owns scheduling and cancellation while OCR/model orchestration remains in the
same application service used by the HTTP workflow.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException

from application.job_worker import JobCancelled, RetryableJobError, TerminalJobError, TaskRegistry
from domain.questions.source import MAX_FULL_PAPER_QUESTIONS_PER_BATCH, MAX_QUESTIONS_PER_BATCH


def build_textbook_registry(processing_service: Any) -> TaskRegistry:
    registry = TaskRegistry()

    def _run(call: Callable[[], Any], cancellation_check: Callable[[], bool]) -> Any:
        if cancellation_check():
            raise JobCancelled()
        try:
            result = call()
        except HTTPException as error:
            details = {"statusCode": error.status_code}
            if error.status_code in {408, 425, 429, 500, 502, 503, 504}:
                raise RetryableJobError(str(error.detail), details=details) from error
            raise TerminalJobError(str(error.detail), details=details) from error
        if cancellation_check():
            raise JobCancelled()
        return result

    @registry.decorator("textbook.upload.complete")
    def complete(payload: dict[str, Any], cancellation_check: Callable[[], bool]) -> Any:
        result = _run(
            lambda: processing_service.complete_upload(
                payload["uploadId"],
                cancellation_check=cancellation_check,
                question_limit=(
                    MAX_FULL_PAPER_QUESTIONS_PER_BATCH
                    if payload.get("generateFullPaper", False) else MAX_QUESTIONS_PER_BATCH
                ),
            ),
            cancellation_check,
        )
        if not payload.get("generateFullPaper", False):
            return result
        full_paper = _run(
            lambda: processing_service.generate_full_paper(
                payload["uploadId"], cancellation_check=cancellation_check,
            ),
            cancellation_check,
        )
        if isinstance(result, dict) and isinstance(full_paper, dict):
            return {
                **result,
                "questionPayload": full_paper.get("questionPayload") or result.get("questionPayload"),
                "questionPayloads": full_paper.get("questionPayloads") or result.get("questionPayloads"),
                "batches": full_paper.get("batches") or result.get("batches"),
                "fullPaper": full_paper.get("summary"),
            }
        return result

    @registry.decorator("textbook.batch.process")
    def process_batch(payload: dict[str, Any], cancellation_check: Callable[[], bool]) -> Any:
        return _run(
            lambda: processing_service.process_batch(
                payload["uploadId"], payload["batchId"],
                bool(payload.get("force", False)),
                refresh_ocr=bool(payload.get("refreshOcr", False)),
                cancellation_check=cancellation_check,
            ),
            cancellation_check,
        )

    @registry.decorator("textbook.paper.generate")
    def generate_full_paper(payload: dict[str, Any], cancellation_check: Callable[[], bool]) -> Any:
        """Generate the bounded full paper while preserving per-batch failures."""
        return _run(
            lambda: processing_service.generate_full_paper(
                payload["uploadId"],
                cancellation_check=cancellation_check,
            ),
            cancellation_check,
        )

    return registry
