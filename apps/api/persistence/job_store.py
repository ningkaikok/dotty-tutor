"""Durable storage for generic background work.

上传记录和可执行任务是两个不同的生命周期：一条教材上传可以产生多个任务，任务也可以
来自未来的批处理或重新审核。因此这里不修改 ``upload_jobs``，而是提供独立的最小 Job
Store。时间戳统一使用 Unix seconds，便于队列审计和重试契约共享。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from persistence.base import DatabaseStore
from persistence.database import decode_json
from persistence.legacy_cleanup_guards import (
    job_is_tombstoned,
    lock_upload_for_legacy_write,
    payload_targets_any_tombstoned_output,
    source_upload_id_for_payload,
    tombstoned_ids,
)
from persistence.schema import background_jobs

JOB_STATUSES = {"queued", "running", "succeeded", "failed", "cancelled"}


class JobStore(DatabaseStore):
    """保存后台任务，并在数据库层约束租约和状态迁移。

    claim 在事务中使用 PostgreSQL 的 ``FOR UPDATE SKIP LOCKED``，让多个 Worker
    可以安全地抢占不同任务。
    """

    def _row_to_job(self, row: Any) -> dict[str, Any]:
        return {
            "jobId": row["job_id"],
            "jobType": row["job_type"],
            "idempotencyKey": row["idempotency_key"],
            "payload": decode_json(row["payload_json"]),
            "status": row["status"],
            "maxAttempts": row["max_attempts"],
            "attemptCount": row["attempt_count"],
            "lastError": decode_json(row["last_error_json"]) if row["last_error_json"] is not None else None,
            "cancelRequested": bool(row["cancel_requested"]),
            "leaseOwner": row["lease_owner"],
            "leaseExpiresAt": row["lease_expires_at"],
            "result": decode_json(row["result_json"]) if row["result_json"] is not None else None,
            "progress": int(row["progress"] or 0),
            "message": row["message"] or "",
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "startedAt": row["started_at"],
            "completedAt": row["completed_at"],
        }

    def create_job(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        max_attempts: int = 3,
        job_id: str | None = None,
        now: float | None = None,
        connection: Connection | None = None,
        reject_payload_mismatch: bool = False,
    ) -> dict[str, Any]:
        """创建 queued 任务；相同幂等键始终返回原任务而不重复入队。"""
        if not job_type or not job_type.strip():
            raise ValueError("job_type 不能为空")
        if max_attempts < 1:
            raise ValueError("max_attempts 必须大于 0")
        self._ensure_initialized()
        timestamp = time.time() if now is None else now
        values = {
            "job_id": job_id or uuid.uuid4().hex,
            "job_type": job_type,
            "idempotency_key": idempotency_key,
            "payload_json": payload,
            "status": "queued",
            "max_attempts": max_attempts,
            "attempt_count": 0,
            "cancel_requested": False,
            "created_at": timestamp,
            "updated_at": timestamp,
            "progress": 0,
            "message": "等待 Worker 处理",
        }
        if connection is None:
            with self.engine.begin() as active:
                result = self.create_job(
                    job_type, payload, idempotency_key=idempotency_key,
                    max_attempts=max_attempts, job_id=job_id, now=timestamp,
                    connection=active, reject_payload_mismatch=reject_payload_mismatch,
                )
            return self.get_job(result["jobId"]) or result

        upload_id = payload.get("uploadId") or source_upload_id_for_payload(connection, payload)
        marker = None
        if upload_id:
            _upload, marker = lock_upload_for_legacy_write(connection, str(upload_id))
        if payload_targets_any_tombstoned_output(connection, payload):
            raise ValueError("该历史内容已撤回，不能为旧对象创建生成任务")
        if values["job_id"] in tombstoned_ids(marker, "jobIds") or job_is_tombstoned(connection, values["job_id"]):
            raise ValueError("该历史任务已撤回，不能重新使用")

        if idempotency_key:
            existing = connection.execute(select(background_jobs).where(
                background_jobs.c.idempotency_key == idempotency_key,
            ).with_for_update()).mappings().first()
            if existing:
                if str(existing["job_id"]) in tombstoned_ids(marker, "jobIds") or job_is_tombstoned(connection, str(existing["job_id"])):
                    raise ValueError("该历史任务已撤回，不能重新使用")
                if reject_payload_mismatch and (
                    existing["job_type"] != job_type
                    or decode_json(existing["payload_json"]) != payload
                ):
                    raise ValueError("幂等键已用于不同任务请求")
                return self._row_to_job(existing)

        statement = postgresql_insert(background_jobs).values(**values)
        if idempotency_key:
            statement = statement.on_conflict_do_nothing(
                index_elements=[background_jobs.c.idempotency_key],
            )
        inserted = connection.execute(statement.returning(background_jobs)).mappings().first()
        if inserted is not None:
            return self._row_to_job(inserted)
        # A concurrent request may have won the idempotency key after our read.
        existing = connection.execute(select(background_jobs).where(
            background_jobs.c.idempotency_key == idempotency_key,
        ).with_for_update()).mappings().first()
        if existing is None:
            raise IntegrityError(
                "background job insert conflict", {}, RuntimeError("conflicting row was not visible"),
            )
        if str(existing["job_id"]) in tombstoned_ids(marker, "jobIds") or job_is_tombstoned(connection, str(existing["job_id"])):
            raise ValueError("该历史任务已撤回，不能重新使用")
        if reject_payload_mismatch and (
            existing["job_type"] != job_type
            or decode_json(existing["payload_json"]) != payload
        ):
            raise ValueError("幂等键已用于不同任务请求")
        return self._row_to_job(existing)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(background_jobs).where(background_jobs.c.job_id == job_id)
            ).mappings().first()
            if row and job_is_tombstoned(connection, job_id):
                return None
        return self._row_to_job(row) if row else None

    def get_by_idempotency_key(
        self, idempotency_key: str, *, connection: Connection | None = None, lock: bool = False,
    ) -> dict[str, Any] | None:
        if not idempotency_key:
            return None
        self._ensure_initialized()
        if connection is not None:
            query = select(background_jobs).where(background_jobs.c.idempotency_key == idempotency_key)
            if lock:
                query = query.with_for_update()
            row = connection.execute(query).mappings().first()
            if row and job_is_tombstoned(connection, str(row["job_id"])):
                return None
        else:
            with self.engine.connect() as active:
                row = active.execute(
                    select(background_jobs).where(background_jobs.c.idempotency_key == idempotency_key)
                ).mappings().first()
                if row and job_is_tombstoned(active, str(row["job_id"])):
                    return None
        return self._row_to_job(row) if row else None

    def latest_for_payload(self, job_type: str, key: str, value: str) -> dict[str, Any] | None:
        """Recover one domain's latest task without scanning or exposing other jobs."""
        self._ensure_initialized()
        with self.engine.connect() as connection:
            row = connection.execute(select(background_jobs).where(
                background_jobs.c.job_type == job_type,
                background_jobs.c.payload_json[key].as_string() == value,
            ).order_by(background_jobs.c.created_at.desc()).limit(1)).mappings().first()
            if row and job_is_tombstoned(connection, str(row["job_id"])):
                return None
        return self._row_to_job(row) if row else None

    def list_jobs(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """按创建时间倒序读取任务，供 CLI 和后续状态查询复用。"""
        if status is not None and status not in JOB_STATUSES:
            raise ValueError(f"未知任务状态：{status}")
        self._ensure_initialized()
        query = select(background_jobs).order_by(background_jobs.c.created_at.desc()).limit(min(limit, 200))
        if status:
            query = query.where(background_jobs.c.status == status)
        with self.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
            visible = []
            for row in rows:
                if job_is_tombstoned(connection, str(row["job_id"])):
                    continue
                visible.append(row)
        return [self._row_to_job(row) for row in visible]

    def recover_expired_leases(self, *, now: float | None = None) -> int:
        """回收已过期租约，并遵守最大尝试次数。

        Worker 崩溃也算一次失败：仍有预算的任务回到队列，已耗尽预算的任务进入
        failed，避免一个确定性崩溃任务无限循环占用队列。
        """
        self._ensure_initialized()
        timestamp = time.time() if now is None else now
        with self.engine.begin() as connection:
            cancelled = connection.execute(
                update(background_jobs)
                .where(
                    background_jobs.c.status == "running",
                    background_jobs.c.lease_expires_at.is_not(None),
                    background_jobs.c.lease_expires_at <= timestamp,
                    background_jobs.c.cancel_requested.is_(True),
                )
                .values(
                    status="cancelled", lease_owner=None, lease_expires_at=None,
                    completed_at=timestamp, updated_at=timestamp,
                )
            )
            exhausted = connection.execute(
                update(background_jobs)
                .where(
                    background_jobs.c.status == "running",
                    background_jobs.c.lease_expires_at.is_not(None),
                    background_jobs.c.lease_expires_at <= timestamp,
                    background_jobs.c.cancel_requested.is_(False),
                    background_jobs.c.attempt_count >= background_jobs.c.max_attempts,
                )
                .values(
                    status="failed", lease_owner=None, lease_expires_at=None,
                    completed_at=timestamp, updated_at=timestamp,
                    last_error_json={
                        "type": "WorkerLeaseExpired",
                        "code": "worker_lease_expired",
                        "message": "Worker 租约过期且已达到最大尝试次数",
                        "retryable": True,
                    },
                    progress=100, message="Worker 中断，处理失败",
                )
            )
            requeued = connection.execute(
                update(background_jobs)
                .where(
                    background_jobs.c.status == "running",
                    background_jobs.c.lease_expires_at.is_not(None),
                    background_jobs.c.lease_expires_at <= timestamp,
                    background_jobs.c.cancel_requested.is_(False),
                    background_jobs.c.attempt_count < background_jobs.c.max_attempts,
                )
                .values(
                    status="queued", lease_owner=None, lease_expires_at=None,
                    updated_at=timestamp,
                )
            )
        return (
            int(cancelled.rowcount or 0)
            + int(exhausted.rowcount or 0)
            + int(requeued.rowcount or 0)
        )

    def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: float = 60.0,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """原子领取一个任务并递增 attemptCount。

        行锁和 ``SKIP LOCKED`` 都由 PostgreSQL 在事务内执行，避免多个 Worker
        领取同一任务。
        """
        if not worker_id:
            raise ValueError("worker_id 不能为空")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds 必须大于 0")
        self._ensure_initialized()
        timestamp = time.time() if now is None else now
        with self.engine.begin() as connection:
            eligible = or_(
                and_(
                    background_jobs.c.status == "queued",
                    background_jobs.c.attempt_count < background_jobs.c.max_attempts,
                ),
                and_(
                    background_jobs.c.status == "running",
                    background_jobs.c.lease_expires_at.is_not(None),
                    background_jobs.c.lease_expires_at <= timestamp,
                ),
            )
            query = (
                select(background_jobs)
                .where(eligible)
                .order_by(background_jobs.c.created_at, background_jobs.c.job_id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            row = connection.execute(query).mappings().first()
            if not row:
                return None
            connection.execute(
                update(background_jobs)
                .where(background_jobs.c.job_id == row["job_id"])
                .values(
                    status="running",
                    attempt_count=background_jobs.c.attempt_count + 1,
                    lease_owner=worker_id,
                    lease_expires_at=timestamp + lease_seconds,
                    started_at=row["started_at"] or timestamp,
                    updated_at=timestamp, progress=5, message="Worker 已开始处理",
                )
            )
            claimed = connection.execute(
                select(background_jobs).where(background_jobs.c.job_id == row["job_id"])
            ).mappings().first()
            return self._row_to_job(claimed) if claimed else None

    def is_cancel_requested(self, job_id: str) -> bool:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            value = connection.scalar(
                select(background_jobs.c.cancel_requested).where(background_jobs.c.job_id == job_id)
            )
        return bool(value)

    def execution_is_current(
        self,
        job_id: str,
        worker_id: str,
        attempt_count: int,
        *,
        now: float | None = None,
    ) -> bool:
        """Fence a claimed execution by owner, attempt, live lease, and cancellation."""
        timestamp = time.time() if now is None else now
        self._ensure_initialized()
        with self.engine.connect() as connection:
            value = connection.scalar(
                select(background_jobs.c.job_id).where(
                    background_jobs.c.job_id == job_id,
                    background_jobs.c.status == "running",
                    background_jobs.c.lease_owner == worker_id,
                    background_jobs.c.attempt_count == attempt_count,
                    background_jobs.c.lease_expires_at > timestamp,
                    background_jobs.c.cancel_requested.is_(False),
                )
            )
        return value is not None

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        *,
        lease_seconds: float = 60.0,
        expected_attempt_count: int | None = None,
        now: float | None = None,
    ) -> bool:
        """续租仍由指定 Worker 持有的任务；过期后不复活旧执行者。"""
        timestamp = time.time() if now is None else now
        self._ensure_initialized()
        conditions = [
            background_jobs.c.job_id == job_id,
            background_jobs.c.status == "running",
            background_jobs.c.lease_owner == worker_id,
            background_jobs.c.lease_expires_at > timestamp,
        ]
        if expected_attempt_count is not None:
            conditions.append(background_jobs.c.attempt_count == expected_attempt_count)
        with self.engine.begin() as connection:
            changed = connection.execute(
                update(background_jobs)
                .where(*conditions)
                .values(lease_expires_at=timestamp + lease_seconds, updated_at=timestamp)
            )
        return bool(changed.rowcount)

    def request_cancel(self, job_id: str, *, now: float | None = None, connection: Connection | None = None) -> dict[str, Any] | None:
        """排队任务立即取消，运行任务设置标记并由 handler 在安全点退出。"""
        self._ensure_initialized()
        timestamp = time.time() if now is None else now
        if connection is None:
            with self.engine.begin() as active:
                self.request_cancel(job_id, now=timestamp, connection=active)
            return self.get_job(job_id)
        connection.execute(
            update(background_jobs)
            .where(background_jobs.c.job_id == job_id, background_jobs.c.status == "queued")
            .values(
                status="cancelled", cancel_requested=True, completed_at=timestamp,
                updated_at=timestamp, progress=100, message="已取消",
            )
        )
        connection.execute(
            update(background_jobs)
            .where(background_jobs.c.job_id == job_id, background_jobs.c.status == "running")
            .values(
                cancel_requested=True,
                updated_at=timestamp,
                message="已请求取消，将在安全点停止",
            )
        )
        row = connection.execute(select(background_jobs).where(background_jobs.c.job_id == job_id)).mappings().first()
        return self._row_to_job(row) if row else None

    cancel_job = request_cancel

    def retry_job(self, job_id: str, *, now: float | None = None) -> dict[str, Any] | None:
        """给失败任务追加一次人工重试预算并放回队列。

        ``attemptCount`` 是审计事实，不能归零；人工重试通过增加 ``maxAttempts``
        表达新的执行授权。上一次结构化错误保留到成功或下一次失败，便于排障。
        """
        self._ensure_initialized()
        timestamp = time.time() if now is None else now
        with self.engine.begin() as connection:
            preview = connection.execute(select(
                background_jobs.c.payload_json,
            ).where(background_jobs.c.job_id == job_id)).mappings().first()
            preview_payload = decode_json(preview["payload_json"]) if preview else {}
            upload_id = (
                (preview_payload.get("uploadId") or source_upload_id_for_payload(connection, preview_payload))
                if isinstance(preview_payload, dict) else None
            )
            marker = None
            if upload_id:
                _upload, marker = lock_upload_for_legacy_write(connection, str(upload_id))
            if job_id in tombstoned_ids(marker, "jobIds") or job_is_tombstoned(connection, job_id):
                return None
            row = connection.execute(
                select(
                    background_jobs.c.status,
                    background_jobs.c.attempt_count,
                    background_jobs.c.max_attempts,
                ).where(background_jobs.c.job_id == job_id).with_for_update()
            ).mappings().first()
            if not row or row["status"] != "failed":
                return None
            connection.execute(
                update(background_jobs)
                .where(background_jobs.c.job_id == job_id, background_jobs.c.status == "failed")
                .values(
                    status="queued", cancel_requested=False,
                    lease_owner=None, lease_expires_at=None, completed_at=None,
                    updated_at=timestamp, progress=0, message="等待人工重试",
                    max_attempts=max(row["max_attempts"], row["attempt_count"] + 1),
                )
            )
        return self.get_job(job_id)

    def update_progress(
        self,
        job_id: str,
        *,
        progress: int,
        message: str = "",
        worker_id: str | None = None,
        expected_attempt_count: int | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """更新任务展示进度；worker_id 存在时只允许租约持有者写入。"""
        self._ensure_initialized()
        timestamp = time.time() if now is None else now
        conditions = [background_jobs.c.job_id == job_id]
        if worker_id:
            conditions.extend([
                background_jobs.c.status == "running",
                background_jobs.c.lease_owner == worker_id,
                background_jobs.c.lease_expires_at > timestamp,
            ])
            if expected_attempt_count is not None:
                conditions.append(background_jobs.c.attempt_count == expected_attempt_count)
        with self.engine.begin() as connection:
            connection.execute(
                update(background_jobs).where(*conditions).values(
                    progress=max(0, min(int(progress), 100)),
                    message=message[:1000], updated_at=timestamp,
                )
            )
        return self.get_job(job_id)

    def mark_cancelled(
        self,
        job_id: str,
        worker_id: str,
        *,
        expected_attempt_count: int | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        """由当前租约 Worker 将运行任务收敛为 cancelled。"""
        self._ensure_initialized()
        timestamp = time.time() if now is None else now
        conditions = [
            background_jobs.c.job_id == job_id,
            background_jobs.c.status == "running",
            background_jobs.c.lease_owner == worker_id,
        ]
        if expected_attempt_count is not None:
            conditions.append(background_jobs.c.attempt_count == expected_attempt_count)
        with self.engine.begin() as connection:
            changed = connection.execute(
                update(background_jobs)
                .where(*conditions)
                .values(
                    status="cancelled", cancel_requested=True,
                    lease_owner=None, lease_expires_at=None,
                    completed_at=timestamp, updated_at=timestamp,
                    progress=100, message="已取消",
                )
            )
        if changed.rowcount != 1:
            raise ValueError("任务不存在，或租约已不属于当前 Worker")
        return self.get_job(job_id)  # type: ignore[return-value]

    def complete_success(
        self,
        job_id: str,
        worker_id: str,
        result: Any = None,
        *,
        expected_attempt_count: int | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        """由租约持有者完成任务；取消请求优先于成功结果。"""
        self._ensure_initialized()
        timestamp = time.time() if now is None else now
        conditions = [
            background_jobs.c.job_id == job_id,
            background_jobs.c.status == "running",
            background_jobs.c.lease_owner == worker_id,
            background_jobs.c.lease_expires_at > timestamp,
            background_jobs.c.cancel_requested.is_(False),
        ]
        if expected_attempt_count is not None:
            conditions.append(background_jobs.c.attempt_count == expected_attempt_count)
        with self.engine.begin() as connection:
            changed = connection.execute(
                update(background_jobs)
                .where(*conditions)
                .values(
                    status="succeeded", result_json=result,
                    last_error_json=None,
                    lease_owner=None, lease_expires_at=None,
                    completed_at=timestamp, updated_at=timestamp,
                    progress=100, message="处理完成",
                )
            )
        if changed.rowcount != 1:
            raise ValueError("任务不存在、已取消，或租约已不属于当前 Worker")
        return self.get_job(job_id)  # type: ignore[return-value]

    def complete_failure(
        self,
        job_id: str,
        worker_id: str,
        error: dict[str, Any],
        *,
        retryable: bool,
        expected_attempt_count: int | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        """记录结构化错误；可重试且未达上限时回到 queued，否则 failed。"""
        self._ensure_initialized()
        timestamp = time.time() if now is None else now
        with self.engine.begin() as connection:
            conditions = [
                background_jobs.c.job_id == job_id,
                background_jobs.c.status == "running",
                background_jobs.c.lease_owner == worker_id,
                background_jobs.c.lease_expires_at > timestamp,
            ]
            if expected_attempt_count is not None:
                conditions.append(background_jobs.c.attempt_count == expected_attempt_count)
            row = connection.execute(
                select(
                    background_jobs.c.attempt_count,
                    background_jobs.c.max_attempts,
                    background_jobs.c.cancel_requested,
                )
                .where(*conditions)
                .with_for_update()
            ).mappings().first()
            if not row:
                raise ValueError("任务不存在，或租约已不属于当前 Worker")
            if row["cancel_requested"]:
                changed = connection.execute(
                    update(background_jobs)
                    .where(*conditions, background_jobs.c.cancel_requested.is_(True))
                    .values(
                        status="cancelled", lease_owner=None, lease_expires_at=None,
                        completed_at=timestamp, updated_at=timestamp, progress=100,
                        message="已取消",
                    )
                )
                if changed.rowcount != 1:
                    raise ValueError("取消中的任务执行尝试已变化")
            else:
                should_retry = retryable and row["attempt_count"] < row["max_attempts"]
                values: dict[str, Any] = {
                    "status": "queued" if should_retry else "failed",
                    "last_error_json": error,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "updated_at": timestamp,
                }
                if not should_retry:
                    values["completed_at"] = timestamp
                    values["progress"] = 100
                    values["message"] = "处理失败"
                else:
                    values["progress"] = 0
                    values["message"] = "处理失败，等待重试"
                changed = connection.execute(
                    update(background_jobs)
                    .where(*conditions, background_jobs.c.cancel_requested.is_(False))
                    .values(**values)
                )
                if changed.rowcount != 1:
                    raise ValueError("任务不存在，或执行尝试已不属于当前 Worker")
        return self.get_job(job_id)  # type: ignore[return-value]
