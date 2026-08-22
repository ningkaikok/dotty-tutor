"""Contract tests for the reusable textbook task registration."""

from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import app
from fastapi import HTTPException
from fastapi.testclient import TestClient
from persistence.job_store import JobStore

from application.job_worker import JobCancelled, RetryableJobError, TerminalJobError
from application.textbook_jobs import build_textbook_registry


class _Service:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def complete_upload(self, *args, **kwargs):
        self.calls.append(("complete", args, kwargs))
        return {"ok": True}

    def process_batch(self, *args, **kwargs):
        self.calls.append(("batch", args, kwargs))
        return {"ok": True}

    def generate_full_paper(self, *args, **kwargs):
        self.calls.append(("full-paper", args, kwargs))
        return {"summary": {"totalBatches": 0}}


class TextbookJobRegistryTests(unittest.TestCase):
    def test_transient_http_failures_are_retried_but_validation_failures_are_terminal(self) -> None:
        service = _Service()
        registry = build_textbook_registry(service)
        service.complete_upload = lambda *args, **kwargs: (_ for _ in ()).throw(
            HTTPException(status_code=503, detail="OCR provider unavailable")
        )
        with self.assertRaises(RetryableJobError):
            registry.get("textbook.upload.complete")({"uploadId": "u1"}, lambda: False)

        service.complete_upload = lambda *args, **kwargs: (_ for _ in ()).throw(
            HTTPException(status_code=422, detail="invalid PDF")
        )
        with self.assertRaises(TerminalJobError):
            registry.get("textbook.upload.complete")({"uploadId": "u1"}, lambda: False)

    def test_handlers_delegate_to_service_and_honor_cancellation(self) -> None:
        service = _Service()
        registry = build_textbook_registry(service)
        def check() -> bool:
            return False
        self.assertEqual(
            registry.get("textbook.upload.complete")({"uploadId": "u1"}, check),
            {"ok": True},
        )
        self.assertEqual(
            registry.get("textbook.batch.process")(
                {"uploadId": "u1", "batchId": "b1", "force": True}, check,
            ),
            {"ok": True},
        )
        self.assertEqual(service.calls[0][1], ("u1",))
        self.assertEqual(service.calls[1][1], ("u1", "b1", True))
        self.assertEqual(
            registry.get("textbook.paper.generate")({"uploadId": "u1"}, check),
            {"summary": {"totalBatches": 0}},
        )
        self.assertEqual(service.calls[2][1], ("u1",))
        self.assertNotIn("max_questions", service.calls[2][2])
        with self.assertRaises(JobCancelled):
            registry.get("textbook.upload.complete")({"uploadId": "u1"}, lambda: True)

    def test_upload_completion_can_run_full_paper_in_same_worker_task(self) -> None:
        service = _Service()
        def generate_full_paper(*args, **kwargs):
            service.calls.append(("full-paper", args, kwargs))
            return {
                "summary": {"questionCount": 8},
                "questionPayload": {"question": {"id": "q1"}},
                "questionPayloads": [{"question": {"id": "q1"}}],
                "batches": [],
            }
        service.generate_full_paper = generate_full_paper
        registry = build_textbook_registry(service)

        result = registry.get("textbook.upload.complete")(
            {"uploadId": "u1", "generateFullPaper": True}, lambda: False,
        )

        self.assertEqual(result["fullPaper"], {"questionCount": 8})
        self.assertEqual([call[0] for call in service.calls], ["complete", "full-paper"])
        self.assertEqual(service.calls[0][2]["question_limit"], 20)

    def test_batch_route_enqueues_one_idempotent_job(self) -> None:
        """HTTP 请求只入队；重复点击必须返回同一个后台任务。"""
        with TemporaryDirectory() as directory:
            store = JobStore(database_url=f"sqlite+pysqlite:///{directory}/jobs.sqlite3")
            try:
                with (
                    patch("api.routers.textbook_routes.job_store", store),
                    patch("api.routers.textbook_routes.upload_job", return_value={}),
                ):
                    client = TestClient(app)
                    path = "/api/uploads/u1/batches/b1/process?force=true&refreshOcr=false"
                    first = client.post(path)
                    second = client.post(path)

                self.assertEqual(first.status_code, 202)
                self.assertEqual(second.status_code, 202)
                self.assertEqual(first.json()["jobId"], second.json()["jobId"])
                self.assertEqual(first.json()["status"], "queued")
                self.assertEqual(first.json()["attemptCount"], 0)
                self.assertFalse(first.json()["cancelRequested"])
            finally:
                store.close()

    def test_batch_route_does_not_duplicate_an_active_whole_paper_job(self) -> None:
        with TemporaryDirectory() as directory:
            store = JobStore(database_url=f"sqlite+pysqlite:///{directory}/jobs.sqlite3")
            try:
                store.create_job("textbook.paper.generate", {"uploadId": "u1"})
                with (
                    patch("api.routers.textbook_routes.job_store", store),
                    patch("api.routers.textbook_routes.upload_job", return_value={}),
                ):
                    client = TestClient(app)
                    response = client.post("/api/uploads/u1/batches/b1/process")

                self.assertEqual(response.status_code, 409)
                self.assertIn("整本试卷任务", response.json()["message"])
            finally:
                store.close()

    def test_full_paper_route_uses_stable_upload_idempotency_key(self) -> None:
        with TemporaryDirectory() as directory:
            store = JobStore(database_url=f"sqlite+pysqlite:///{directory}/jobs.sqlite3")
            try:
                with (
                    patch("api.routers.textbook_routes.job_store", store),
                    patch(
                        "api.routers.textbook_routes.upload_job",
                        return_value={"status": "complete", "result": {"batches": []}},
                    ),
                ):
                    client = TestClient(app)
                    first = client.post("/api/uploads/u1/full-paper")
                    second = client.post("/api/uploads/u1/full-paper")

                self.assertEqual(first.status_code, 202)
                self.assertEqual(second.status_code, 202)
                self.assertEqual(first.json()["jobId"], second.json()["jobId"])
                queued = store.get_job(first.json()["jobId"])
                self.assertEqual(queued["payload"], {"uploadId": "u1"})
            finally:
                store.close()

    def test_full_paper_summary_returns_initial_report_while_job_is_queued(self) -> None:
        with TemporaryDirectory() as directory:
            store = JobStore(database_url=f"sqlite+pysqlite:///{directory}/jobs.sqlite3")
            try:
                preview = {
                    "status": "complete",
                    "result": {
                        "batches": [{"id": "batch-1"}, {"id": "batch-2"}],
                        "questionPayloads": [{"question": {"id": "preview-1"}}],
                    },
                }
                with (
                    patch("api.routers.textbook_routes.job_store", store),
                    patch("api.routers.textbook_routes.upload_job", return_value=preview),
                ):
                    client = TestClient(app)
                    queued = client.post("/api/uploads/u1/full-paper")
                    report = client.get("/api/uploads/u1/full-paper/summary")

                self.assertEqual(queued.status_code, 202)
                self.assertEqual(report.status_code, 200)
                self.assertEqual(report.json()["job"]["status"], "queued")
                self.assertEqual(report.json()["summary"]["totalBatches"], 2)
                self.assertEqual(report.json()["summary"]["processedBatches"], 0)
                self.assertEqual(len(report.json()["questionPayloads"]), 1)
            finally:
                store.close()
