"""Focused tests for the reusable textbook PDF application service."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from textbook_routes import pdf_uploads, process_pdf_batch


class TextbookProcessingTests(unittest.TestCase):
    def test_queued_batch_uses_its_page_range_and_becomes_switchable(self) -> None:
        """A later batch includes the previous page to recover split questions."""
        with TemporaryDirectory() as directory:
            payload = {
                "question": {"id": "q2"},
                "lessonSteps": [],
                "architecture": {},
                "modelRun": {"provider": "mock", "model": "test", "fallback": False},
            }
            pdf_uploads["test-upload"] = {
                "status": "complete",
                "directory": Path(directory),
                "result": {
                    "ocrRun": {"provider": "mineru"},
                    "extraction": {"questionCount": 1},
                    "batches": [
                        {"id": "batch-001", "startPage": 1, "endPage": 5, "status": "processed"},
                        {"id": "batch-002", "startPage": 6, "endPage": 10, "status": "queued"},
                    ],
                },
                "batchPayloads": {},
            }
            try:
                with (
                    patch("textbook_routes.ocr_runtime.should_use_mineru", return_value=True),
                    # Patch where the service resolves the dependency, not at
                    # the HTTP facade that merely delegates the call.
                    patch(
                        "textbook_processing.resolve_ocr_text",
                        return_value=("page text", {"provider": "mineru"}),
                    ) as resolve,
                    patch(
                        "question_processing.generate_lesson",
                        return_value=(payload, [], payload["modelRun"]),
                    ),
                    patch("textbook_routes.store.save_questions"),
                    patch("textbook_routes.store.save_lesson"),
                    patch("textbook_routes.store.save_job"),
                ):
                    response = process_pdf_batch("test-upload", "batch-002")
                self.assertEqual(response["questionPayload"]["question"]["id"], "q2")
                self.assertEqual(response["batch"]["status"], "processed")
                self.assertEqual(resolve.call_args.args[3:5], (4, 9))
            finally:
                pdf_uploads.pop("test-upload", None)


if __name__ == "__main__":
    unittest.main()
