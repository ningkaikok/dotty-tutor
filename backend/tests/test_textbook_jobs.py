"""Contract tests for the reusable textbook task registration."""

from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import app
from application.job_worker import JobCancelled
from application.textbook_jobs import build_textbook_registry
from persistence.job_store import JobStore


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
    def test_handlers_delegate_to_service_and_honor_cancellation(self) -> None:
        service = _Service()
        registry = build_textbook_registry(service)
        check = lambda: False
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
