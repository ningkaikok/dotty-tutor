"""Background task handlers for textbook processing.

Handlers deliberately delegate to :class:`TextbookProcessingService`; the worker
owns scheduling and cancellation while OCR/model orchestration remains in the
same application service used by the HTTP workflow.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException

from application.job_worker import JobCancelled, TerminalJobError, TaskRegistry


def build_textbook_registry(processing_service: Any) -> TaskRegistry:
    registry = TaskRegistry()

    def _run(call: Callable[[], Any], cancellation_check: Callable[[], bool]) -> Any:
        if cancellation_check():
            raise JobCancelled()
        try:
            result = call()
        except HTTPException as error:
            raise TerminalJobError(str(error.detail), details={"statusCode": error.status_code}) from error
        if cancellation_check():
            raise JobCancelled()
        return result

    @registry.decorator("textbook.upload.complete")
    def complete(payload: dict[str, Any], cancellation_check: Callable[[], bool]) -> Any:
        return _run(
            lambda: processing_service.complete_upload(
                payload["uploadId"], cancellation_check=cancellation_check,
            ),
            cancellation_check,
        )

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
