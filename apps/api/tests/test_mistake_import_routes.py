from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.mistake_routes import build_mistake_router


class _JobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.created = 0

    def get_by_idempotency_key(self, key: str):
        return next((job for job in self.jobs.values() if job["idempotencyKey"] == key), None)

    def create_job(self, job_type: str, payload: dict, *, idempotency_key: str, max_attempts: int, job_id: str | None = None):
        existing = self.get_by_idempotency_key(idempotency_key)
        if existing:
            return existing
        self.created += 1
        job = {
            "jobId": job_id or f"job-{self.created}",
            "jobType": job_type,
            "idempotencyKey": idempotency_key,
            "status": "queued",
            "progress": 0,
            "message": "等待 Worker 处理",
            "attemptCount": 0,
            "maxAttempts": max_attempts,
            "cancelRequested": False,
            "lastError": None,
            "result": None,
            "createdAt": time.time(),
            "updatedAt": time.time(),
            "startedAt": None,
            "completedAt": None,
        }
        job["idempotencyKey"] = idempotency_key
        self.jobs[job["jobId"]] = job
        return job


class _Store:
    def __init__(self, root: Path) -> None:
        self.mistake_root = root / "mistakes"
        self.mistake_root.mkdir()
        self.items: dict[str, dict] = {}

    def item_directory(self, mistake_id: str) -> Path:
        directory = self.mistake_root / mistake_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def get(self, mistake_id: str):
        return self.items.get(mistake_id)

    def source_path(self, mistake_id: str):
        item = self.get(mistake_id)
        return Path(item["sourceImagePath"]) if item else None


class MistakeImportRouteTests(unittest.TestCase):
    def test_capture_id_is_the_single_enqueue_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            jobs = _JobStore()
            app = FastAPI()
            app.include_router(build_mistake_router(
                store=_Store(Path(directory)),
                recognize=lambda *args, **kwargs: ({}, [], {}, {}),
                job_store=jobs,
            ))
            with TestClient(app) as client:
                first = client.post(
                    "/api/mistakes/import-jobs",
                    files={"file": ("question.png", b"image", "image/png")},
                    data={"captureId": "same-capture"},
                )
                second = client.post(
                    "/api/mistakes/import-jobs",
                    files={"file": ("question.png", b"different-image", "image/png")},
                    data={"captureId": "same-capture"},
                )

            self.assertEqual(first.status_code, 202)
            self.assertEqual(second.status_code, 202)
            self.assertEqual(first.json()["jobId"], second.json()["jobId"])
            self.assertEqual(first.json()["captureId"], "same-capture")
            self.assertEqual(jobs.created, 1)

    def test_same_capture_id_isolated_per_learner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            jobs = _JobStore()
            app = FastAPI()
            app.include_router(build_mistake_router(
                store=_Store(Path(directory)),
                recognize=lambda *args, **kwargs: ({}, [], {}, {}),
                job_store=jobs,
            ))
            with TestClient(app) as client:
                first = client.post(
                    "/api/mistakes/import-jobs",
                    files={"file": ("question.png", b"image", "image/png")},
                    data={"captureId": "same-capture", "learnerId": "learner-1"},
                )
                second = client.post(
                    "/api/mistakes/import-jobs",
                    files={"file": ("question.png", b"image", "image/png")},
                    data={"captureId": "same-capture", "learnerId": "learner-2"},
                )

            self.assertEqual(first.status_code, 202)
            self.assertEqual(second.status_code, 202)
            self.assertNotEqual(first.json()["jobId"], second.json()["jobId"])
            self.assertEqual(jobs.created, 2)

    def test_job_scoped_assets_are_read_next_to_the_stored_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = _Store(root)
            source = store.item_directory("mistake-1") / "job-1" / "source.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"image")
            asset = source.parent / "assets" / "diagram.png"
            asset.parent.mkdir()
            asset.write_bytes(b"diagram")
            store.items["mistake-1"] = {
                "mistakeId": "mistake-1",
                "sourceImagePath": str(source),
                "contentType": "image/png",
                "sourceFilename": "source.png",
            }
            app = FastAPI()
            app.include_router(build_mistake_router(
                store=store,
                recognize=lambda *args, **kwargs: ({}, [], {}, {}),
            ))
            with TestClient(app) as client:
                response = client.get("/api/mistakes/mistake-1/assets/diagram.png")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b"diagram")


if __name__ == "__main__":
    unittest.main()
