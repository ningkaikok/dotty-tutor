"""Focused tests for the reusable textbook PDF application service."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException

from api.routers.textbook_routes import pdf_uploads, processing_service
from application.services.textbook_processing import TextbookProcessingService


class TextbookProcessingTests(unittest.TestCase):
    def test_full_paper_summary_is_bounded_and_resumes_processed_batches(self) -> None:
        """A retry skips persisted successes while recording later batch failures."""
        payload_one = {"question": {"id": "q1", "sourceQuestionKey": "batch-001-q-1"}}
        payload_two = {"question": {"id": "q2", "sourceQuestionKey": "batch-002-q-1"}}
        job = {
            "uploadId": "full-paper-upload",
            "status": "complete",
            "result": {
                "batches": [
                    {"id": "batch-001", "status": "processed", "fullPaperProcessed": True},
                    {"id": "batch-002", "status": "queued"},
                    {"id": "batch-003", "status": "queued"},
                ],
                "batchQuestionKeys": {"batch-001": ["batch-001-q-1"]},
                "questionPayloads": [payload_one],
                "questionPayload": payload_one,
            },
            "batchQuestionKeys": {"batch-001": ["batch-001-q-1"]},
            "batchPayloads": {"batch-001-q-1": payload_one},
        }

        class Registry:
            def get(self, _upload_id):
                return job

            def update(self, current, status, progress, message):
                current["status"] = status
                current["progress"] = progress
                current["message"] = message

        service = TextbookProcessingService(store=object(), upload_registry=Registry(), ocr_runtime=object())
        def process_batch(_upload_id, batch_id, **_kwargs):
            if batch_id == "batch-002":
                job["batchPayloads"]["batch-002-q-1"] = payload_two
                job["batchQuestionKeys"]["batch-002"] = ["batch-002-q-1"]
                return {"questionPayloads": [payload_two]}
            raise HTTPException(status_code=422, detail="OCR 失败")

        with patch.object(service, "process_batch", side_effect=process_batch) as process_batch_mock:
            result = service.generate_full_paper("full-paper-upload", max_questions=100)

        summary = result["summary"]
        self.assertEqual(summary["totalBatches"], 3)
        self.assertEqual(summary["processedBatches"], 3)
        self.assertEqual(summary["succeededBatches"], 1)
        self.assertEqual(summary["failedBatches"], 1)
        self.assertEqual(summary["skippedBatches"], 1)
        self.assertEqual(summary["quarantinedQuestions"], 0)
        self.assertEqual(summary["questionCount"], 2)
        self.assertEqual([item["status"] for item in summary["batches"]], ["skipped", "succeeded", "failed"])
        self.assertEqual(process_batch_mock.call_count, 2)

    def test_full_paper_stops_model_work_at_question_limit(self) -> None:
        """The question cap must stop later model calls, not merely trim the response."""
        job = {
            "status": "complete",
            "result": {
                "batches": [
                    {"id": "batch-001", "status": "queued"},
                    {"id": "batch-002", "status": "queued"},
                ],
                "batchQuestionKeys": {},
                "questionPayloads": [],
            },
            "batchQuestionKeys": {},
            "batchPayloads": {},
        }

        class Registry:
            def get(self, _upload_id):
                return job

            def update(self, current, status, progress, message):
                current.update(status=status, progress=progress, message=message)

        service = TextbookProcessingService(store=object(), upload_registry=Registry(), ocr_runtime=object())

        def process_batch(_upload_id, batch_id, **kwargs):
            self.assertEqual(kwargs["question_limit"], 1)
            key = f"{batch_id}-q-1"
            generated = {"question": {"id": "q1", "sourceQuestionKey": key}}
            job["batchPayloads"][key] = generated
            job["batchQuestionKeys"][batch_id] = [key]
            return {"questionPayloads": [generated]}

        with patch.object(service, "process_batch", side_effect=process_batch) as process_batch_mock:
            result = service.generate_full_paper("u1", max_questions=1)

        self.assertEqual(process_batch_mock.call_count, 1)
        self.assertEqual(result["summary"]["questionCount"], 1)
        self.assertTrue(result["summary"]["limitReached"])

    def test_batch_payload_order_uses_source_order_not_lexical_key_order(self) -> None:
        payload_two = {"question": {"id": "q2"}}
        payload_ten = {"question": {"id": "q10"}}
        job = {
            "batchPayloads": {"batch-001-q-10": payload_ten, "batch-001-q-2": payload_two},
            "batchQuestionKeys": {"batch-001": ["batch-001-q-2", "batch-001-q-10"]},
        }
        result = {"batches": [{"id": "batch-001"}], "batchQuestionKeys": {}}

        ordered = TextbookProcessingService._ordered_batch_payloads(job, result)

        self.assertEqual([item["question"]["id"] for item in ordered], ["q2", "q10"])

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
                    patch("api.routers.textbook_routes.ocr_runtime.should_use_mineru", return_value=True),
                    # Patch where the service resolves the dependency, not at
                    # the HTTP facade that merely delegates the call.
                    patch(
                        "application.services.textbook_processing.resolve_routed_ocr_source",
                        return_value=("page text", {"provider": "mineru"}),
                    ) as resolve,
                    patch(
                        "application.services.question_processing.generate_lesson",
                        return_value=(payload, [], payload["modelRun"]),
                    ),
                    patch(
                        "application.services.question_processing.review_lesson_payload",
                        side_effect=lambda item, _source, _images, _cards: (
                            item,
                            {"provider": "test"},
                        ),
                    ),
                    patch(
                        "application.services.question_processing.apply_question_quality_gate",
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
                    patch("api.routers.textbook_routes.store.save_questions"),
                    patch("api.routers.textbook_routes.store.save_lesson"),
                    patch("api.routers.textbook_routes.store.save_job"),
                ):
                    response = processing_service.process_batch("test-upload", "batch-002")
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
                        "application.services.textbook_processing._generate_validated_question",
                        return_value=(new_payload, [], new_payload["modelRun"], {"provider": "test"}),
                    ),
                    patch("application.services.textbook_processing.TextbookProcessingService._persist_lessons"),
                    patch("api.routers.textbook_routes.store.save_job"),
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
