from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from application.job_worker import (
    JobCancelled,
    JobWorker,
    RetryableJobError,
    TaskRegistry,
)
from application.mistake_jobs import (
    build_mistake_registry,
    cleanup_queued_mistake_capture,
    mistake_id_for_capture,
)


class _Store:
    def __init__(self, root: Path) -> None:
        self.mistake_root = root / "mistakes"
        self.mistake_root.mkdir()
        self.items: dict[str, dict] = {}

    def get(self, mistake_id: str):
        return self.items.get(mistake_id)

    def item_directory(self, mistake_id: str) -> Path:
        directory = self.mistake_root / mistake_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def create(self, item: dict):
        self.items[item["mistakeId"]] = item
        return item

    def create_capture_with_status(self, item: dict):
        existing = self.items.get(item["mistakeId"])
        if existing is not None:
            return existing, False
        return self.create(item), True

    def delete_capture(self, mistake_id: str, *, source_image_path: str) -> bool:
        item = self.items.get(mistake_id)
        if not item or item.get("sourceImagePath") != source_image_path:
            return False
        del self.items[mistake_id]
        return True


class _WorkerStore:
    def __init__(self, payload: dict, *, cancel_after: int = 6) -> None:
        self.job = {
            "jobId": "job-race",
            "jobType": "mistake.image.import",
            "payload": payload,
            "status": "queued",
            "cancelRequested": False,
        }
        self.cancel_checks = 0
        self.cancel_after = cancel_after

    def recover_expired_leases(self):
        return 0

    def claim_next(self, worker_id: str, *, lease_seconds: float):
        if self.job["status"] != "queued":
            return None
        self.job.update(status="running", leaseOwner=worker_id)
        return self.job

    def is_cancel_requested(self, job_id: str) -> bool:
        self.cancel_checks += 1
        # Let the handler finish its final-write checks; cancellation arrives
        # in the Worker-to-complete_success gap.
        return self.cancel_checks >= self.cancel_after

    def update_progress(self, *args, **kwargs):
        return self.job

    def heartbeat(self, *args, **kwargs):
        return True

    def get_job(self, job_id: str):
        return self.job

    def mark_cancelled(self, job_id: str, worker_id: str):
        self.job.update(status="cancelled", cancelRequested=True)
        return self.job

    def complete_success(self, *args, **kwargs):
        raise AssertionError("cancelled job must not commit success")


def payload(source_path: Path) -> dict:
    return {
        "captureId": "capture-1",
        "mistakeId": mistake_id_for_capture("capture-1"),
        "learnerId": "learner-1",
        "filename": "question.png",
        "contentType": "image/png",
        "sourceText": "题目",
        "originalAnswer": "答案",
        "sourcePath": str(source_path),
    }


class MistakeJobTests(unittest.TestCase):
    def test_cancelled_before_ocr_does_not_create_mistake(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = _Store(root)
            source = store.item_directory(mistake_id_for_capture("capture-1")) / "source.png"
            source.write_bytes(b"image")
            calls: list[str] = []

            def recognize(*args, **kwargs):
                calls.append("recognize")
                return {}, [], {}, {}

            handler = build_mistake_registry(store=store, recognize=recognize).get("mistake.image.import")
            with self.assertRaises(JobCancelled):
                handler(payload(source), lambda: True)

            self.assertEqual(calls, [])
            self.assertEqual(store.items, {})
            self.assertFalse(source.parent.exists())

    def test_failed_retry_reuses_capture_and_creates_one_item(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = _Store(root)
            source = store.item_directory(mistake_id_for_capture("capture-1")) / "source.png"
            source.write_bytes(b"image")
            attempts = 0

            def recognize(*args, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise RuntimeError("provider unavailable")
                return (
                    {"question": {"chapter": "章节", "knowledgePoint": "知识点"}},
                    [],
                    {},
                    {},
                )

            handler = build_mistake_registry(store=store, recognize=recognize).get("mistake.image.import")
            with self.assertRaises(RetryableJobError):
                handler(payload(source), lambda: False)
            self.assertTrue(source.exists())

            created = handler(payload(source), lambda: False)
            repeated = handler(payload(source), lambda: False)
            self.assertEqual(created.value["mistakeId"], repeated.value["mistakeId"])
            self.assertEqual(attempts, 2)
            self.assertEqual(len(store.items), 1)

    def test_cancel_requested_after_model_stops_before_final_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = _Store(root)
            source = store.item_directory(mistake_id_for_capture("capture-1")) / "source.png"
            source.write_bytes(b"image")
            cancelled = False

            def recognize(*args, **kwargs):
                nonlocal cancelled
                kwargs["stage_check"]("ocr_after")
                cancelled = True
                return (
                    {"question": {}},
                    [],
                    {},
                    {},
                )

            handler = build_mistake_registry(store=store, recognize=recognize).get("mistake.image.import")
            with self.assertRaises(JobCancelled):
                handler(payload(source), lambda: cancelled)
            self.assertEqual(store.items, {})

    def test_cancel_requested_after_final_write_removes_only_new_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = _Store(root)
            source = store.item_directory(mistake_id_for_capture("capture-1")) / "source.png"
            source.write_bytes(b"image")
            checks = 0

            def cancellation_check() -> bool:
                nonlocal checks
                checks += 1
                # file_saved, recognition complete, final-write-before are safe;
                # cancellation arrives immediately after the row is inserted.
                return checks >= 4

            def recognize(*args, **kwargs):
                return (
                    {"question": {"chapter": "章节", "knowledgePoint": "知识点"}},
                    [],
                    {},
                    {},
                )

            handler = build_mistake_registry(store=store, recognize=recognize).get("mistake.image.import")
            with self.assertRaises(JobCancelled):
                handler(payload(source), cancellation_check)
            self.assertEqual(store.items, {})
            self.assertFalse(source.parent.exists())

    def test_queued_cancel_cleanup_is_limited_to_the_learner_scoped_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture_id = "capture-queued"
            learner_id = "learner-1"
            mistake_id = mistake_id_for_capture(capture_id, learner_id)
            source = root / "mistakes" / mistake_id / "source.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"image")
            job = {
                "jobType": "mistake.image.import",
                "payload": {
                    "captureId": capture_id,
                    "learnerId": learner_id,
                    "mistakeId": mistake_id,
                    "sourcePath": str(source),
                },
            }

            self.assertTrue(cleanup_queued_mistake_capture(job, data_root=root))
            self.assertFalse(source.parent.exists())
            self.assertFalse(cleanup_queued_mistake_capture(job, data_root=root))

            other = root / "mistakes" / mistake_id_for_capture(capture_id, "learner-2")
            other.mkdir(parents=True)
            unsafe = {**job, "payload": {**job["payload"], "sourcePath": str(other / "source.png")}}
            self.assertFalse(cleanup_queued_mistake_capture(unsafe, data_root=root))
            self.assertTrue(other.exists())

            nested = root / "mistakes" / mistake_id / "job-1" / "source.png"
            nested.parent.mkdir(parents=True)
            nested.write_bytes(b"nested")
            nested_job = {**job, "jobId": "job-1", "payload": {**job["payload"], "sourcePath": str(nested)}}
            self.assertTrue(cleanup_queued_mistake_capture(nested_job, data_root=root))
            self.assertFalse(nested.parent.exists())
            self.assertTrue(other.exists())

    def test_worker_cancellation_after_handler_return_compensates_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mistake_store = _Store(root)
            mistake_id = mistake_id_for_capture("capture-race")
            source = mistake_store.item_directory(mistake_id) / "source.png"
            source.write_bytes(b"image")
            worker_payload = payload(source)
            worker_payload.update(captureId="capture-race", mistakeId=mistake_id)
            worker_store = _WorkerStore(worker_payload)
            handler = build_mistake_registry(
                store=mistake_store,
                recognize=lambda *args, **kwargs: (
                    {"question": {"chapter": "章节", "knowledgePoint": "知识点"}},
                    [],
                    {},
                    {},
                ),
            ).get("mistake.image.import")
            registry = TaskRegistry()
            registry.register("mistake.image.import", handler)

            result = JobWorker(worker_store, registry, worker_id="worker-a").run_once()

            self.assertEqual(result["status"], "cancelled")
            self.assertEqual(mistake_store.items, {})
            self.assertFalse(source.parent.exists())

    def test_existing_capture_from_same_job_is_compensated_after_lease_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mistake_store = _Store(root)
            mistake_id = mistake_id_for_capture("capture-handoff")
            source = mistake_store.item_directory(mistake_id) / "source.png"
            source.write_bytes(b"image")
            mistake_store.items[mistake_id] = {
                "mistakeId": mistake_id,
                "sourceImagePath": str(source),
            }
            worker_payload = payload(source)
            worker_payload.update(captureId="capture-handoff", mistakeId=mistake_id)
            worker_store = _WorkerStore(worker_payload, cancel_after=2)
            registry = TaskRegistry()
            registry.register("mistake.image.import", build_mistake_registry(
                store=mistake_store,
                recognize=lambda *args, **kwargs: ({}, [], {}, {}),
            ).get("mistake.image.import"))

            result = JobWorker(worker_store, registry, worker_id="worker-reclaimed").run_once()

            self.assertEqual(result["status"], "cancelled")
            self.assertEqual(mistake_store.items, {})
            self.assertFalse(source.parent.exists())

    def test_losing_capture_never_cleans_the_winner_on_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mistake_store = _Store(root)
            capture_id = "capture-shared"
            learner_id = "learner-1"
            mistake_id = mistake_id_for_capture(capture_id, learner_id)
            winner_source = mistake_store.item_directory(mistake_id) / "winner" / "source.png"
            winner_source.parent.mkdir(parents=True)
            winner_source.write_bytes(b"winner")
            loser_source = mistake_store.item_directory(mistake_id) / "loser" / "source.png"
            loser_source.parent.mkdir(parents=True)
            loser_source.write_bytes(b"loser")
            mistake_store.items[mistake_id] = {
                "mistakeId": mistake_id,
                "sourceImagePath": str(winner_source),
            }
            worker_payload = payload(loser_source)
            worker_payload.update(
                captureId=capture_id,
                learnerId=learner_id,
                mistakeId=mistake_id,
                jobId="loser",
            )
            worker_store = _WorkerStore(worker_payload, cancel_after=2)
            registry = TaskRegistry()
            registry.register("mistake.image.import", build_mistake_registry(
                store=mistake_store,
                recognize=lambda *args, **kwargs: ({}, [], {}, {}),
            ).get("mistake.image.import"))

            result = JobWorker(worker_store, registry, worker_id="worker-loser").run_once()

            self.assertEqual(result["status"], "cancelled")
            self.assertEqual(mistake_store.items[mistake_id]["sourceImagePath"], str(winner_source))
            self.assertTrue(winner_source.exists())
            self.assertFalse(loser_source.exists())


if __name__ == "__main__":
    unittest.main()
