from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from application.job_worker import (
    JobCancelled,
    JobWorker,
    RetryableJobError,
    TaskRegistry,
    TerminalJobError,
)
from persistence.job_store import JobStore


class BackgroundJobTests(unittest.TestCase):
    def make_store(self) -> tuple[JobStore, tempfile.TemporaryDirectory[str]]:
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        return JobStore(f"sqlite+pysqlite:///{root / 'jobs.sqlite3'}", root), directory

    def test_idempotency_and_claim_increment_attempt_count(self) -> None:
        store, directory = self.make_store()
        try:
            first = store.create_job("demo", {"value": 1}, idempotency_key="same", now=10)
            duplicate = store.create_job("demo", {"value": 999}, idempotency_key="same", now=11)
            self.assertEqual(first["jobId"], duplicate["jobId"])
            claimed = store.claim_next("worker-a", now=12)
            self.assertEqual(claimed["status"], "running")
            self.assertEqual(claimed["attemptCount"], 1)
            self.assertEqual(claimed["leaseOwner"], "worker-a")
        finally:
            store.close()
            directory.cleanup()

    def test_retryable_failure_requeues_then_terminal_failure_stops(self) -> None:
        store, directory = self.make_store()
        try:
            job = store.create_job("demo", {}, max_attempts=2, now=1)
            store.claim_next("worker-a", now=2)
            retried = store.complete_failure(
                job["jobId"], "worker-a", {"type": "TimeoutError", "retryable": True}, retryable=True, now=3
            )
            self.assertEqual(retried["status"], "queued")
            self.assertEqual(retried["attemptCount"], 1)
            store.claim_next("worker-a", now=4)
            failed = store.complete_failure(
                job["jobId"], "worker-a", {"type": "TimeoutError", "retryable": True}, retryable=True, now=5
            )
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["lastError"]["type"], "TimeoutError")
        finally:
            store.close()
            directory.cleanup()

    def test_expired_lease_is_reclaimed_and_cancel_request_is_cooperative(self) -> None:
        store, directory = self.make_store()
        try:
            job = store.create_job("demo", {}, now=1)
            store.claim_next("dead-worker", lease_seconds=1, now=2)
            reclaimed = store.claim_next("live-worker", lease_seconds=10, now=4)
            self.assertEqual(reclaimed["jobId"], job["jobId"])
            self.assertEqual(reclaimed["attemptCount"], 2)
            store.request_cancel(job["jobId"], now=5)
            self.assertTrue(store.get_job(job["jobId"])["cancelRequested"])
            self.assertEqual(store.mark_cancelled(job["jobId"], "live-worker", now=6)["status"], "cancelled")
        finally:
            store.close()
            directory.cleanup()

    def test_cancelled_expired_lease_and_max_attempt_boundary(self) -> None:
        store, directory = self.make_store()
        try:
            queued = store.create_job("queued", {})
            self.assertEqual(store.request_cancel(queued["jobId"], now=2)["status"], "cancelled")
            self.assertIsNone(store.claim_next("worker-a", now=3))

            running = store.create_job("running", {}, now=4)
            store.claim_next("worker-a", lease_seconds=1, now=5)
            store.request_cancel(running["jobId"], now=5.5)
            self.assertEqual(store.recover_expired_leases(now=6), 1)
            self.assertEqual(store.get_job(running["jobId"])["status"], "cancelled")

            terminal = store.create_job("terminal", {}, max_attempts=1, now=7)
            store.claim_next("worker-a", now=8)
            result = store.complete_failure(
                terminal["jobId"], "worker-a", {"retryable": True}, retryable=True, now=9
            )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["attemptCount"], 1)

            crashed = store.create_job("crashed", {}, max_attempts=1, now=10)
            store.claim_next("dead-worker", lease_seconds=1, now=11)
            self.assertEqual(store.recover_expired_leases(now=13), 1)
            crashed_result = store.get_job(crashed["jobId"])
            self.assertEqual(crashed_result["status"], "failed")
            self.assertEqual(crashed_result["lastError"]["code"], "worker_lease_expired")
        finally:
            store.close()
            directory.cleanup()

    def test_manual_retry_preserves_attempt_history_and_adds_one_budget(self) -> None:
        store, directory = self.make_store()
        try:
            job = store.create_job("demo", {}, max_attempts=1, now=1)
            store.claim_next("worker-a", now=2)
            failed = store.complete_failure(
                job["jobId"],
                "worker-a",
                {"code": "terminal", "message": "bad input", "retryable": False},
                retryable=False,
                now=3,
            )
            self.assertEqual(failed["attemptCount"], 1)
            retried = store.retry_job(job["jobId"], now=4)
            self.assertEqual(retried["status"], "queued")
            self.assertEqual(retried["attemptCount"], 1)
            self.assertEqual(retried["maxAttempts"], 2)
            self.assertEqual(retried["lastError"]["code"], "terminal")
            self.assertIsNone(store.retry_job(job["jobId"], now=5))
        finally:
            store.close()
            directory.cleanup()

    def test_worker_passes_payload_and_cancellation_check(self) -> None:
        store, directory = self.make_store()
        try:
            registry = TaskRegistry()
            seen: list[object] = []

            def handler(payload, cancellation_check):
                seen.extend((payload, cancellation_check()))
                return {"ok": True}

            registry.register("demo", handler)
            store.create_job("demo", {"answer": 42})
            result = JobWorker(store, registry, worker_id="worker-a").run_once()
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(seen, [{"answer": 42}, False])
        finally:
            store.close()
            directory.cleanup()

    def test_long_handler_renews_short_lease_without_duplicate_claim(self) -> None:
        first_store, directory = self.make_store()
        second_store = None
        try:
            second_store = JobStore(
                f"sqlite+pysqlite:///{Path(directory.name) / 'jobs.sqlite3'}",
                Path(directory.name),
            )
            registry = TaskRegistry()
            started = threading.Event()
            release = threading.Event()
            calls: list[str] = []

            def handler(payload, cancellation_check):
                calls.append(payload["worker"])
                started.set()
                self.assertFalse(cancellation_check())
                self.assertTrue(release.wait(2))
                return {"done": True}

            registry.register("long", handler)
            job = first_store.create_job("long", {"worker": "first"}, max_attempts=2)
            first_worker = JobWorker(first_store, registry, worker_id="worker-a", lease_seconds=0.3)
            second_worker = JobWorker(second_store, registry, worker_id="worker-b", lease_seconds=0.3)
            result_holder: list[dict] = []
            first_thread = threading.Thread(target=lambda: result_holder.append(first_worker.run_once()))
            first_thread.start()
            self.assertTrue(started.wait(1))
            # This is longer than the lease but shorter than the 2-second
            # handler timeout; the heartbeat should have renewed it twice.
            time.sleep(0.55)
            self.assertIsNone(second_worker.run_once())
            self.assertEqual(first_store.get_job(job["jobId"])["status"], "running")
            release.set()
            first_thread.join(2)
            self.assertFalse(first_thread.is_alive())
            self.assertEqual(result_holder[0]["status"], "succeeded")
            self.assertEqual(calls, ["first"])
        finally:
            if second_store is not None:
                second_store.close()
            first_store.close()
            directory.cleanup()

    def test_worker_classifies_failures_and_cancellation(self) -> None:
        store, directory = self.make_store()
        try:
            registry = TaskRegistry()

            def retry(payload, check):
                raise RetryableJobError("temporary", details={"provider": "mock"})

            def terminal(payload, check):
                raise TerminalJobError("bad payload")

            registry.register("retry", retry)
            registry.register("terminal", terminal)
            retry_job = store.create_job("retry", {}, max_attempts=2)
            store.create_job("terminal", {})
            worker = JobWorker(store, registry, worker_id="worker-a")
            self.assertEqual(worker.run_once()["status"], "queued")
            self.assertTrue(store.get_job(retry_job["jobId"])["lastError"]["retryable"])
            self.assertEqual(worker.run_once()["status"], "failed")
            self.assertEqual(worker.run_once()["status"], "failed")

            cancelled_job = store.create_job("cancel", {})
            registry.register("cancel", lambda payload, check: (_ for _ in ()).throw(JobCancelled()))
            self.assertEqual(worker.run_once()["status"], "cancelled")
            store.request_cancel(cancelled_job["jobId"])
        finally:
            store.close()
            directory.cleanup()


if __name__ == "__main__":
    unittest.main()
