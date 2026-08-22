"""Minimal PostgreSQL-backed background Worker.

Worker 只负责领取任务、调用注册表中的 handler 和收敛执行状态；业务 handler 仍由应用
服务实现，因此本模块不依赖 FastAPI。handler 的唯一契约是
``handler(payload, cancellation_check)``，其中 cancellation_check 是无参数布尔函数。
"""

from __future__ import annotations

import importlib
import os
import threading
import uuid
from dataclasses import dataclass
from threading import Event
from typing import Any, Callable, Mapping

from persistence.job_store import JobStore

from observability import log_event

CancellationCheck = Callable[[], bool]
JobHandler = Callable[[dict[str, Any], CancellationCheck], Any]


class JobCancelled(Exception):
    """handler 在安全点主动退出时使用。"""


class JobFailure(Exception):
    """可显式标记 retryable/terminal 的结构化任务失败。"""

    retryable = False
    code = "job_failure"

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})


class RetryableJobError(JobFailure):
    retryable = True
    code = "retryable_error"


class TerminalJobError(JobFailure):
    retryable = False
    code = "terminal_error"


@dataclass(frozen=True)
class RegisteredTask:
    name: str
    handler: JobHandler


class TaskRegistry:
    """按稳定 jobType 注册 handler，避免 Worker 通过条件分支耦合业务。"""

    def __init__(self) -> None:
        self._tasks: dict[str, RegisteredTask] = {}

    def register(self, job_type: str, handler: JobHandler, *, replace: bool = False) -> JobHandler:
        if not job_type or not job_type.strip():
            raise ValueError("job_type 不能为空")
        if job_type in self._tasks and not replace:
            raise ValueError(f"任务类型已经注册：{job_type}")
        self._tasks[job_type] = RegisteredTask(job_type, handler)
        return handler

    def decorator(self, job_type: str, *, replace: bool = False) -> Callable[[JobHandler], JobHandler]:
        def _register(handler: JobHandler) -> JobHandler:
            return self.register(job_type, handler, replace=replace)

        return _register

    def get(self, job_type: str) -> JobHandler | None:
        task = self._tasks.get(job_type)
        return task.handler if task else None

    def __contains__(self, job_type: str) -> bool:
        return job_type in self._tasks


class JobWorker:
    """单进程任务执行器；数据库租约负责跨进程互斥与崩溃恢复。"""

    def __init__(
        self,
        store: JobStore,
        registry: TaskRegistry,
        *,
        worker_id: str | None = None,
        lease_seconds: float = 60.0,
        poll_interval: float = 1.0,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds 必须大于 0")
        if poll_interval < 0:
            raise ValueError("poll_interval 不能小于 0")
        self.store = store
        self.registry = registry
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
        self.lease_seconds = lease_seconds
        self.poll_interval = poll_interval

    @staticmethod
    def _error_payload(error: Exception, *, retryable: bool, code: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": type(error).__name__,
            "code": code or ("retryable_error" if retryable else "terminal_error"),
            "message": str(error)[:1000],
            "retryable": retryable,
        }
        if isinstance(error, JobFailure) and error.details:
            payload["details"] = error.details
        return payload

    def run_once(self) -> dict[str, Any] | None:
        """执行一个任务；无任务时返回 None，便于 CLI 和单元测试复用。"""
        self.store.recover_expired_leases()
        job = self.store.claim_next(self.worker_id, lease_seconds=self.lease_seconds)
        if job is None:
            return None
        job_id = job["jobId"]
        if self.store.is_cancel_requested(job_id):
            return self.store.mark_cancelled(job_id, self.worker_id)
        handler = self.registry.get(job["jobType"])
        if handler is None:
            error = TerminalJobError(f"未注册任务类型：{job['jobType']}")
            return self.store.complete_failure(
                job_id, self.worker_id, self._error_payload(error, retryable=False, code="unknown_job_type"),
                retryable=False,
            )

        def cancellation_check() -> bool:
            return self.store.is_cancel_requested(job_id)

        self.store.update_progress(
            job_id,
            progress=10,
            message="正在执行教材处理任务",
            worker_id=self.worker_id,
        )

        # 长任务必须在自己的租约过期前续租，否则另一个 Worker 会把仍在执行的
        # handler 当成崩溃任务重新领取。续租线程只维护租约，不参与结果收敛；即使
        # 数据库短暂不可用，也不能用后台线程异常覆盖 handler 的主结果。
        heartbeat_stop = threading.Event()
        lease_lost = threading.Event()
        heartbeat_errors: list[Exception] = []
        heartbeat_interval = max(self.lease_seconds / 3.0, 0.01)

        def renew_lease() -> None:
            while not heartbeat_stop.wait(heartbeat_interval):
                try:
                    if not self.store.heartbeat(
                        job_id,
                        self.worker_id,
                        lease_seconds=self.lease_seconds,
                    ):
                        heartbeat_errors.append(RuntimeError("任务租约已不属于当前 Worker"))
                        lease_lost.set()
                        return
                except Exception as error:  # Handler result remains the primary outcome.
                    heartbeat_errors.append(error)
                    lease_lost.set()
                    return

        heartbeat_thread = threading.Thread(
            target=renew_lease,
            name=f"job-lease-{job_id[:12]}",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            result = handler(job["payload"], cancellation_check)
            # 租约丢失表示数据库已经不再承认当前 Worker 的所有权。此时不能继续
            # 写入成功/失败状态，否则会覆盖接管该任务的新 Worker 的执行结果。
            if lease_lost.is_set():
                return self.store.get_job(job_id)
            if cancellation_check():
                return self.store.mark_cancelled(job_id, self.worker_id)
            try:
                return self.store.complete_success(job_id, self.worker_id, result)
            except ValueError:
                # A cancellation can race the final check. Re-read the row and
                # honor the request instead of leaving an orphaned lease.
                current = self.store.get_job(job_id)
                if current and current["status"] == "running" and current["cancelRequested"]:
                    return self.store.mark_cancelled(job_id, self.worker_id)
                raise
        except JobCancelled as error:
            if lease_lost.is_set():
                return self.store.get_job(job_id)
            return self.store.mark_cancelled(job_id, self.worker_id)
        except JobFailure as error:
            if lease_lost.is_set():
                return self.store.get_job(job_id)
            if cancellation_check():
                return self.store.mark_cancelled(job_id, self.worker_id)
            return self.store.complete_failure(
                job_id,
                self.worker_id,
                self._error_payload(error, retryable=error.retryable, code=error.code),
                retryable=error.retryable,
            )
        except Exception as error:  # Unknown failures are bounded retries for transient runtimes.
            if lease_lost.is_set():
                return self.store.get_job(job_id)
            if cancellation_check():
                return self.store.mark_cancelled(job_id, self.worker_id)
            return self.store.complete_failure(
                job_id,
                self.worker_id,
                self._error_payload(error, retryable=True, code="unhandled_error"),
                retryable=True,
            )
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join()
            if heartbeat_errors:
                error = heartbeat_errors[0]
                log_event(
                    "background_job.heartbeat.failed",
                    level=30,
                    job_id=job_id,
                    worker_id=self.worker_id,
                    error_type=type(error).__name__,
                    error=str(error)[:300],
                )

    def run_forever(self, *, stop_event: Event | None = None) -> None:
        """轮询直到 stop_event 被设置；默认可被 SIGTERM/KeyboardInterrupt 结束。"""
        stop_event = stop_event or Event()
        while not stop_event.is_set():
            if self.run_once() is None:
                stop_event.wait(self.poll_interval)


def load_registry(spec: str | None) -> TaskRegistry:
    """加载 ``module:attribute`` 注册表，CLI 默认使用空注册表。"""
    if not spec:
        return TaskRegistry()
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("registry 必须是 module:attribute")
    value = getattr(importlib.import_module(module_name), attribute)
    if not isinstance(value, TaskRegistry):
        raise TypeError("registry attribute 必须是 TaskRegistry")
    return value


def main(argv: list[str] | None = None) -> None:
    """命令行入口：``python -m application.job_worker``。"""
    import argparse

    parser = argparse.ArgumentParser(description="Run Dotty background jobs")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--lease-seconds", type=float, default=60.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--once", action="store_true", help="process at most one job")
    parser.add_argument("--registry", help="module:attribute containing a TaskRegistry")
    args = parser.parse_args(argv)

    store = JobStore(database_url=args.database_url)
    try:
        worker = JobWorker(
            store,
            load_registry(args.registry),
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
            poll_interval=args.poll_interval,
        )
        if args.once:
            worker.run_once()
        else:
            worker.run_forever()
    except KeyboardInterrupt:
        return
    finally:
        store.close()


if __name__ == "__main__":  # pragma: no cover - exercised by CLI smoke tests
    main()
