"""Focused tests for the reusable textbook PDF application service."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from textbook_routes import pdf_uploads, process_pdf_batch, processing_service


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
                    "sourceFingerprint": "0" * 64,
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
                        "textbook_processing.resolve_routed_ocr_source",
                        return_value=("page text", {"provider": "mineru"}),
                    ) as resolve,
                    patch(
                        "question_processing.generate_lesson",
                        return_value=(payload, [], payload["modelRun"]),
                    ),
                    patch(
                        "question_processing.review_lesson_payload",
                        side_effect=lambda item, _source, _images, _cards: (
                            item,
                            {"provider": "test"},
                        ),
                    ),
                    patch(
                        "question_processing.apply_question_quality_gate",
                        side_effect=lambda item, _source, _images: (
                            item.update({
                                "quality": {
                                    "status": "ready",
                                    "errors": [],
                                    "warnings": [],
                                    "validatorVersion": "test-v1",
                                },
                            })
                            or item["quality"]
                        ),
                    ),
                    patch("textbook_routes.store.save_questions"),
                    patch("textbook_routes.store.save_lesson"),
                    patch("textbook_routes.store.save_job"),
                ):
                    response = process_pdf_batch("test-upload", "batch-002")
                self.assertEqual(response["questionPayload"]["question"]["id"], "q2")
                self.assertEqual(response["batch"]["status"], "processed")
                self.assertEqual(resolve.call_args.kwargs["start_page"], 4)
                self.assertEqual(resolve.call_args.kwargs["end_page"], 9)
            finally:
                pdf_uploads.pop("test-upload", None)

    def test_question_regeneration_replaces_only_selected_question(self) -> None:
        """Single-question repair must keep the other questions and stable source key."""
        with TemporaryDirectory() as directory:
            source_key = "batch-001-q-2"
            first = {
                "question": {
                    "id": "old-1",
                    "sourceBatchId": "batch-001",
                    "sourceQuestionKey": "batch-001-q-1",
                },
                "modelRun": {"provider": "mock", "model": "test"},
            }
            old_target = {
                "question": {
                    "id": "old-2",
                    "sourceBatchId": "batch-001",
                    "sourceQuestionKey": source_key,
                },
                "modelRun": {"provider": "mock", "model": "test"},
            }
            job = {
                "uploadId": "single-question-upload",
                "status": "complete",
                "filename": "source.pdf",
                "directory": Path(directory),
                "batchPayloads": {
                    "batch-001-q-1": first,
                    source_key: old_target,
                },
                "batchGuideCards": {"batch-001-q-1": [], source_key: []},
                "batchQuestionKeys": {"batch-001": ["batch-001-q-1", source_key]},
                "result": {
                    "sourceFingerprint": "0" * 64,
                    "batches": [{"id": "batch-001", "startPage": 1, "endPage": 5, "status": "processed"}],
                    "questionPayloads": [first, old_target],
                    "questionPayload": first,
                },
            }
            pdf_uploads[job["uploadId"]] = job
            new_payload = {
                "question": {
                    "id": "new-2",
                    "sourceBatchId": "batch-001",
                    "sourceQuestionKey": "batch-001-q-2",
                },
                "modelRun": {"provider": "test", "model": "test"},
            }
            try:
                with (
                    patch.object(
                        processing_service,
                        "_load_batch_sources",
                        return_value=("2、题目", {"provider": "mineru"}, Path(directory), [("2", "2、题目", [])]),
                    ),
                    patch(
                        "textbook_processing._generate_validated_question",
                        return_value=(new_payload, [], new_payload["modelRun"], {"provider": "test"}),
                    ),
                    patch("textbook_processing.TextbookProcessingService._persist_lessons"),
                    patch("textbook_routes.store.save_job"),
                ):
                    response = processing_service.regenerate_question(job["uploadId"], source_key)
                self.assertEqual(response["questionPayload"]["question"]["id"], "new-2")
                self.assertEqual(response["questionPayload"]["question"]["sourceQuestionKey"], source_key)
                self.assertIs(job["batchPayloads"]["batch-001-q-1"], first)
                self.assertEqual(job["batchQuestionKeys"]["batch-001"], ["batch-001-q-1", source_key])
                self.assertEqual(job["result"]["questionPayloads"], [first, new_payload])
            finally:
                pdf_uploads.pop(job["uploadId"], None)


if __name__ == "__main__":
    unittest.main()
