"""Durable PostgreSQL store for bounded model comparison runs and human ratings."""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Engine,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

from persistence.base import DatabaseStore
from persistence.database import decode_json
from persistence.schema import json_document

quality_review_runs_metadata = MetaData()

quality_review_run_control = Table(
    "quality_review_run_control",
    quality_review_runs_metadata,
    Column("control_id", String(16), primary_key=True),
    Column("active_run_id", String(64), nullable=True),
    CheckConstraint("control_id = 'active'", name="ck_quality_review_run_control_singleton"),
)

quality_review_runs = Table(
    "quality_review_runs",
    quality_review_runs_metadata,
    Column("run_id", String(64), primary_key=True),
    Column("created_by", String(128), nullable=False),
    Column("created_by_kind", String(40), nullable=False),
    Column("creator_identity_verified", Boolean, nullable=False),
    Column("task_instruction", Text, nullable=False),
    Column("baseline_provider", String(40), nullable=False),
    Column("baseline_model", String(128), nullable=False),
    Column("candidate_provider", String(40), nullable=False),
    Column("candidate_model", String(128), nullable=False),
    Column("per_call_timeout_seconds", Integer, nullable=False),
    Column("total_budget_seconds", Integer, nullable=False),
    Column("max_output_tokens", Integer, nullable=False),
    Column("status", String(24), nullable=False),
    Column("cancel_requested", Boolean, nullable=False, default=False),
    Column("created_at", Float, nullable=False),
    Column("started_at", Float, nullable=True),
    Column("completed_at", Float, nullable=True),
    Column("deadline_at", Float, nullable=False),
    Column("error_json", json_document, nullable=True),
    CheckConstraint("per_call_timeout_seconds BETWEEN 1 AND 60", name="ck_quality_review_run_call_timeout"),
    CheckConstraint("total_budget_seconds BETWEEN 1 AND 300", name="ck_quality_review_run_total_budget"),
    CheckConstraint("max_output_tokens BETWEEN 100 AND 700", name="ck_quality_review_run_tokens"),
    CheckConstraint("status IN ('queued', 'running', 'completed', 'failed', 'cancelled')", name="ck_quality_review_run_status"),
)
Index("ix_quality_review_runs_status_created", quality_review_runs.c.status, quality_review_runs.c.created_at)

quality_review_run_cases = Table(
    "quality_review_run_cases",
    quality_review_runs_metadata,
    Column("run_id", String(64), ForeignKey("quality_review_runs.run_id", ondelete="CASCADE"), primary_key=True),
    Column("case_id", String(64), primary_key=True),
    Column("ordinal", Integer, nullable=False),
    Column("record_version", Integer, nullable=False),
    Column("source_kind", String(40), nullable=False),
    Column("source_fingerprint", String(64), nullable=False),
    Column("input_fingerprint", String(64), nullable=False),
    Column("output_fingerprint", String(64), nullable=False),
    Column("snapshot_json", json_document, nullable=False),
    Column("arm_a_model_slot", String(16), nullable=False),
    Column("arm_b_model_slot", String(16), nullable=False),
    CheckConstraint("record_version > 0", name="ck_quality_review_run_case_version"),
    CheckConstraint("arm_a_model_slot IN ('baseline', 'candidate')", name="ck_quality_review_run_case_arm_a"),
    CheckConstraint("arm_b_model_slot IN ('baseline', 'candidate')", name="ck_quality_review_run_case_arm_b"),
    CheckConstraint("arm_a_model_slot <> arm_b_model_slot", name="ck_quality_review_run_case_pair"),
    UniqueConstraint("run_id", "ordinal", name="uq_quality_review_run_case_ordinal"),
)
Index("ix_quality_review_run_cases_case", quality_review_run_cases.c.case_id, quality_review_run_cases.c.record_version)

quality_review_run_results = Table(
    "quality_review_run_results",
    quality_review_runs_metadata,
    Column("result_id", String(64), primary_key=True),
    Column("run_id", String(64), nullable=False),
    Column("case_id", String(64), nullable=False),
    Column("arm", String(1), nullable=False),
    Column("status", String(16), nullable=False),
    Column("requested_provider", String(40), nullable=False),
    Column("requested_model", String(128), nullable=False),
    Column("actual_provider", String(40), nullable=True),
    Column("actual_model", String(128), nullable=True),
    Column("output_json", json_document, nullable=True),
    Column("duration_ms", Float, nullable=True),
    Column("prompt_tokens", Integer, nullable=True),
    Column("output_tokens", Integer, nullable=True),
    Column("cost", Float, nullable=True),
    Column("error_json", json_document, nullable=True),
    Column("started_at", Float, nullable=True),
    Column("completed_at", Float, nullable=True),
    ForeignKeyConstraint(
        ["run_id", "case_id"],
        ["quality_review_run_cases.run_id", "quality_review_run_cases.case_id"],
        ondelete="CASCADE",
    ),
    UniqueConstraint("run_id", "case_id", "arm", name="uq_quality_review_run_result_arm"),
    CheckConstraint("arm IN ('A', 'B')", name="ck_quality_review_run_result_arm"),
    CheckConstraint("status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')", name="ck_quality_review_run_result_status"),
    CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_quality_review_run_result_duration"),
    CheckConstraint("prompt_tokens IS NULL OR prompt_tokens >= 0", name="ck_quality_review_run_result_prompt_tokens"),
    CheckConstraint("output_tokens IS NULL OR output_tokens >= 0", name="ck_quality_review_run_result_output_tokens"),
    CheckConstraint("cost IS NULL OR cost >= 0", name="ck_quality_review_run_result_cost"),
)
Index("ix_quality_review_run_results_case", quality_review_run_results.c.run_id, quality_review_run_results.c.case_id)

quality_review_run_ratings = Table(
    "quality_review_run_ratings",
    quality_review_runs_metadata,
    Column("rating_id", String(64), primary_key=True),
    Column("run_id", String(64), nullable=False),
    Column("case_id", String(64), nullable=False),
    Column("actor_id", String(128), nullable=False),
    Column("actor_kind", String(40), nullable=False),
    Column("identity_verified", Boolean, nullable=False),
    Column("display_name", String(128), nullable=False, default=""),
    Column("a_pass", Boolean, nullable=False),
    Column("b_pass", Boolean, nullable=False),
    Column("preferred", String(16), nullable=False),
    Column("rationale", Text, nullable=False),
    Column("created_at", Float, nullable=False),
    ForeignKeyConstraint(
        ["run_id", "case_id"],
        ["quality_review_run_cases.run_id", "quality_review_run_cases.case_id"],
        ondelete="CASCADE",
    ),
    UniqueConstraint("run_id", "case_id", "actor_id", name="uq_quality_review_run_rating_actor"),
    CheckConstraint("preferred IN ('A', 'B', 'tie', 'neither')", name="ck_quality_review_run_rating_preference"),
)
Index("ix_quality_review_run_ratings_actor", quality_review_run_ratings.c.run_id, quality_review_run_ratings.c.actor_id)


class QualityReviewRunsStore(DatabaseStore):
    """Persist frozen case runs, arm results and server-attributed user ratings."""

    def __init__(
        self,
        database_url: str | None = None,
        *,
        engine: Engine | None = None,
    ) -> None:
        inherited_url = (
            engine.url.render_as_string(hide_password=False) if engine is not None else None
        )
        super().__init__(database_url=database_url or inherited_url)
        if engine is not None:
            self.engine.dispose()
            self.engine = engine
            self.database_url = str(engine.url)
            self._owns_engine = False
        else:
            self._owns_engine = True

    def close(self) -> None:
        if self._owns_engine:
            super().close()

    def get_active_run_id(self) -> str | None:
        with self.read_connection() as connection:
            return connection.scalar(select(quality_review_run_control.c.active_run_id).where(
                quality_review_run_control.c.control_id == "active",
            ))

    def create_run(
        self,
        run: dict[str, Any],
        cases: list[dict[str, Any]],
        *,
        expected_active_run_id: str | None,
        previous_job_terminal: bool,
    ) -> dict[str, Any]:
        """Insert a run and frozen case/arm assignments under a singleton queue lock."""
        with self.atomic() as connection:
            # Test databases and older installations can have the registered table
            # without the seed row; initialize it idempotently before taking the lock.
            connection.execute(pg_insert(quality_review_run_control).values(
                control_id="active",
                active_run_id=None,
            ).on_conflict_do_nothing(index_elements=[quality_review_run_control.c.control_id]))
            control = connection.execute(
                select(quality_review_run_control)
                .where(quality_review_run_control.c.control_id == "active")
                .with_for_update()
            ).mappings().first()
            if control is None:
                raise RuntimeError("质量运行控制行未初始化")
            active_run_id = control["active_run_id"]
            if active_run_id:
                if active_run_id != expected_active_run_id or not previous_job_terminal:
                    raise RuntimeError("已有有界模型运行正在执行，请稍后再试")
                active = connection.execute(
                    select(quality_review_runs).where(quality_review_runs.c.run_id == active_run_id)
                ).mappings().first()
                if active and active["status"] in {"queued", "running"}:
                    connection.execute(
                        quality_review_runs.update().where(quality_review_runs.c.run_id == active_run_id).values(
                            status="failed",
                            completed_at=run["createdAt"],
                            error_json={"type": "WorkerInterrupted", "message": "前一次共享任务未完成"},
                        )
                    )
                    connection.execute(quality_review_run_results.update().where(
                        quality_review_run_results.c.run_id == active_run_id,
                        quality_review_run_results.c.status.in_(["pending", "running"]),
                    ).values(
                        status="failed",
                        error_json={"type": "WorkerInterrupted", "message": "共享 Worker 未完成的样本不计入比较"},
                        completed_at=run["createdAt"],
                    ))
                connection.execute(
                    quality_review_run_control.update()
                    .where(quality_review_run_control.c.control_id == "active")
                    .values(active_run_id=None)
                )
            connection.execute(quality_review_runs.insert().values(**self._run_values(run)))
            connection.execute(
                quality_review_run_control.update()
                .where(quality_review_run_control.c.control_id == "active")
                .values(active_run_id=run["runId"])
            )
            for item in cases:
                connection.execute(quality_review_run_cases.insert().values(**item["caseValues"]))
                for arm in ("A", "B"):
                    connection.execute(quality_review_run_results.insert().values(
                        result_id=uuid.uuid4().hex,
                        run_id=run["runId"],
                        case_id=item["caseId"],
                        arm=arm,
                        status="pending",
                        requested_provider=item[f"{arm.lower()}Provider"],
                        requested_model=item[f"{arm.lower()}Model"],
                        cost=None,
                    ))
        return self.get_run(run["runId"])  # type: ignore[return-value]

    @staticmethod
    def _run_values(run: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": run["runId"],
            "created_by": run["createdBy"],
            "created_by_kind": run["createdByKind"],
            "creator_identity_verified": run["creatorIdentityVerified"],
            "task_instruction": run["taskInstruction"],
            "baseline_provider": run["baseline"]["provider"],
            "baseline_model": run["baseline"]["model"],
            "candidate_provider": run["candidate"]["provider"],
            "candidate_model": run["candidate"]["model"],
            "per_call_timeout_seconds": run["perCallTimeoutSeconds"],
            "total_budget_seconds": run["totalBudgetSeconds"],
            "max_output_tokens": run["maxOutputTokens"],
            "status": "queued",
            "cancel_requested": False,
            "created_at": run["createdAt"],
            "started_at": None,
            "completed_at": None,
            "deadline_at": run["deadlineAt"],
            "error_json": None,
        }

    def start_run(self, run_id: str, *, now: float | None = None) -> bool:
        timestamp = time.time() if now is None else now
        with self.atomic() as connection:
            changed = connection.execute(
                quality_review_runs.update()
                .where(
                    quality_review_runs.c.run_id == run_id,
                    quality_review_runs.c.status == "queued",
                    quality_review_runs.c.cancel_requested.is_(False),
                    quality_review_runs.c.deadline_at > timestamp,
                )
                .values(status="running", started_at=timestamp)
            )
            return bool(changed.rowcount)

    def fail_queued_run(
        self,
        run_id: str,
        *,
        error: dict[str, Any],
        status: str = "failed",
        now: float | None = None,
    ) -> bool:
        """Close an enqueued business run if its shared Job could not start."""
        if status not in {"failed", "cancelled"}:
            raise ValueError("queued runs can only fail or cancel")
        timestamp = time.time() if now is None else now
        with self.atomic() as connection:
            changed = connection.execute(
                quality_review_runs.update()
                .where(
                    quality_review_runs.c.run_id == run_id,
                    quality_review_runs.c.status == "queued",
                )
                .values(
                    status=status,
                    cancel_requested=status == "cancelled",
                    completed_at=timestamp,
                    error_json=error,
                )
            )
            if not changed.rowcount:
                return False
            connection.execute(
                quality_review_run_results.update()
                .where(
                    quality_review_run_results.c.run_id == run_id,
                    quality_review_run_results.c.status == "pending",
                )
                .values(
                    status=status,
                    error_json=error,
                    completed_at=timestamp,
                )
            )
            connection.execute(
                quality_review_run_control.update()
                .where(
                    quality_review_run_control.c.control_id == "active",
                    quality_review_run_control.c.active_run_id == run_id,
                )
                .values(active_run_id=None)
            )
            return True

    def mark_arm_running(
        self, run_id: str, case_id: str, arm: str, *, now: float | None = None
    ) -> bool:
        timestamp = time.time() if now is None else now
        with self.atomic() as connection:
            owned = connection.execute(select(quality_review_runs.c.run_id).where(
                quality_review_runs.c.run_id == run_id,
                quality_review_runs.c.status == "running",
                quality_review_runs.c.cancel_requested.is_(False),
            )).first()
            if owned is None:
                return False
            changed = connection.execute(
                quality_review_run_results.update()
                .where(
                    quality_review_run_results.c.run_id == run_id,
                    quality_review_run_results.c.case_id == case_id,
                    quality_review_run_results.c.arm == arm,
                    quality_review_run_results.c.status == "pending",
                )
                .values(status="running", started_at=timestamp)
            )
            return bool(changed.rowcount)

    def save_arm_result(
        self,
        run_id: str,
        case_id: str,
        arm: str,
        result: dict[str, Any],
        *,
        now: float | None = None,
    ) -> bool:
        timestamp = time.time() if now is None else now
        with self.atomic() as connection:
            owned = connection.execute(select(quality_review_runs.c.run_id).where(
                quality_review_runs.c.run_id == run_id,
                quality_review_runs.c.status == "running",
            )).first()
            if owned is None:
                return False
            changed = connection.execute(
                quality_review_run_results.update()
                .where(
                    quality_review_run_results.c.run_id == run_id,
                    quality_review_run_results.c.case_id == case_id,
                    quality_review_run_results.c.arm == arm,
                    quality_review_run_results.c.status.in_(["running", "pending"]),
                )
                .values(**self._result_values(result, timestamp))
            )
            return bool(changed.rowcount)

    @staticmethod
    def _result_values(result: dict[str, Any], timestamp: float) -> dict[str, Any]:
        return {
            "status": result["status"],
            "actual_provider": result.get("actualProvider"),
            "actual_model": result.get("actualModel"),
            "output_json": result.get("output"),
            "duration_ms": result.get("durationMs"),
            "prompt_tokens": result.get("promptTokens"),
            "output_tokens": result.get("outputTokens"),
            "cost": None,
            "error_json": result.get("error"),
            "completed_at": timestamp,
        }

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.read_connection() as connection:
            run = connection.execute(select(quality_review_runs).where(
                quality_review_runs.c.run_id == run_id
            )).mappings().first()
            if run is None:
                return None
            cases = connection.execute(select(quality_review_run_cases).where(
                quality_review_run_cases.c.run_id == run_id
            ).order_by(quality_review_run_cases.c.ordinal)).mappings().all()
            results = connection.execute(select(quality_review_run_results).where(
                quality_review_run_results.c.run_id == run_id
            ).order_by(quality_review_run_results.c.case_id, quality_review_run_results.c.arm)).mappings().all()
        results_by_case: dict[str, dict[str, dict[str, Any]]] = {}
        for row in results:
            results_by_case.setdefault(row["case_id"], {})[row["arm"]] = self._result_summary(row)
        return {
            **self._run_summary(run),
            "cases": [
                {
                    **self._case_summary(row),
                    "arms": results_by_case.get(row["case_id"], {}),
                }
                for row in cases
            ],
        }

    def list_runs(self, *, created_by: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = select(quality_review_runs).order_by(quality_review_runs.c.created_at.desc()).limit(min(max(limit, 1), 100))
        if created_by is not None:
            query = query.where(quality_review_runs.c.created_by == created_by)
        with self.read_connection() as connection:
            rows = connection.execute(query).mappings().all()
        return [self._run_summary(row) for row in rows]

    def request_cancel(self, run_id: str, *, now: float | None = None) -> bool:
        timestamp = time.time() if now is None else now
        with self.atomic() as connection:
            run = connection.execute(select(quality_review_runs).where(
                quality_review_runs.c.run_id == run_id
            ).with_for_update()).mappings().first()
            if run is None:
                raise LookupError("模型运行不存在")
            if run["status"] in {"completed", "failed", "cancelled"}:
                return False
            if run["status"] == "queued":
                connection.execute(quality_review_runs.update().where(
                    quality_review_runs.c.run_id == run_id
                ).values(status="cancelled", cancel_requested=True, completed_at=timestamp))
                connection.execute(quality_review_run_results.update().where(
                    quality_review_run_results.c.run_id == run_id,
                    quality_review_run_results.c.status == "pending",
                ).values(
                    status="cancelled",
                    error_json={"type": "UserCancelled", "message": "用户在模型运行开始前取消"},
                    completed_at=timestamp,
                ))
                connection.execute(quality_review_run_control.update().where(
                    quality_review_run_control.c.control_id == "active",
                    quality_review_run_control.c.active_run_id == run_id,
                ).values(active_run_id=None))
            else:
                # Keep the singleton slot until the in-flight provider request reaches its
                # real transport timeout. Releasing it here would allow overlapping work.
                connection.execute(quality_review_runs.update().where(
                    quality_review_runs.c.run_id == run_id
                ).values(cancel_requested=True))
            return True

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        error: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> bool:
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("invalid terminal run status")
        timestamp = time.time() if now is None else now
        with self.atomic() as connection:
            run = connection.execute(select(quality_review_runs).where(
                quality_review_runs.c.run_id == run_id,
                quality_review_runs.c.status == "running",
            ).with_for_update()).mappings().first()
            if run is None:
                return False
            final_status = "cancelled" if run["cancel_requested"] else status
            connection.execute(quality_review_runs.update().where(
                quality_review_runs.c.run_id == run_id,
                quality_review_runs.c.status == "running",
            ).values(status=final_status, completed_at=timestamp, error_json=error))
            if final_status == "cancelled":
                connection.execute(quality_review_run_results.update().where(
                    quality_review_run_results.c.run_id == run_id,
                    quality_review_run_results.c.status.in_(["pending", "running"]),
                ).values(
                    status="cancelled",
                    error_json={"type": "UserCancelled", "message": "用户取消后未开始的样本不计入比较"},
                    completed_at=timestamp,
                ))
            elif final_status == "failed":
                connection.execute(quality_review_run_results.update().where(
                    quality_review_run_results.c.run_id == run_id,
                    quality_review_run_results.c.status.in_(["pending", "running"]),
                ).values(
                    status="failed",
                    error_json={"type": "WorkerFailed", "message": "未完成样本不参与评分或排名"},
                    completed_at=timestamp,
                ))
            connection.execute(quality_review_run_control.update().where(
                quality_review_run_control.c.control_id == "active",
                quality_review_run_control.c.active_run_id == run_id,
            ).values(active_run_id=None))
            return True

    def add_rating(self, rating: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        timestamp = time.time() if now is None else now
        with self.atomic() as connection:
            run = connection.execute(select(quality_review_runs).where(
                quality_review_runs.c.run_id == rating["runId"]
            ).with_for_update()).mappings().first()
            if run is None:
                raise LookupError("模型运行不存在")
            if run["status"] != "completed":
                raise ValueError("仅完成的模型运行可以评分")
            case = connection.execute(select(quality_review_run_cases).where(
                quality_review_run_cases.c.run_id == rating["runId"],
                quality_review_run_cases.c.case_id == rating["caseId"],
            )).mappings().first()
            if case is None:
                raise LookupError("该案例不属于模型运行")
            completed_arms = connection.execute(select(quality_review_run_results.c.arm).where(
                quality_review_run_results.c.run_id == rating["runId"],
                quality_review_run_results.c.case_id == rating["caseId"],
                quality_review_run_results.c.status == "succeeded",
            )).all()
            if {item[0] for item in completed_arms} != {"A", "B"}:
                raise ValueError("模型运行失败或取消的样本不能评分或排名")
            rating_id = uuid.uuid4().hex
            connection.execute(quality_review_run_ratings.insert().values(
                rating_id=rating_id,
                run_id=rating["runId"],
                case_id=rating["caseId"],
                actor_id=rating["actor"]["actorId"],
                actor_kind=rating["actor"]["actorKind"],
                identity_verified=rating["actor"]["identityVerified"],
                display_name=rating["actor"]["displayName"],
                a_pass=rating["aPass"],
                b_pass=rating["bPass"],
                preferred=rating["preferred"],
                rationale=rating["rationale"],
                created_at=timestamp,
            ))
        with self.read_connection() as connection:
            row = connection.execute(select(quality_review_run_ratings).where(
                quality_review_run_ratings.c.rating_id == rating_id,
            )).mappings().one()
        return self._rating_summary(row)

    def list_ratings(self, run_id: str, *, actor_id: str) -> list[dict[str, Any]]:
        with self.read_connection() as connection:
            rows = connection.execute(select(quality_review_run_ratings).where(
                quality_review_run_ratings.c.run_id == run_id,
                quality_review_run_ratings.c.actor_id == actor_id,
            ).order_by(quality_review_run_ratings.c.created_at)).mappings().all()
        return [self._rating_summary(row) for row in rows]

    @staticmethod
    def _run_summary(row: Any) -> dict[str, Any]:
        return {
            "runId": row["run_id"],
            "createdBy": row["created_by"],
            "createdByKind": row["created_by_kind"],
            "creatorIdentityVerified": row["creator_identity_verified"],
            "taskInstruction": row["task_instruction"],
            "baseline": {"provider": row["baseline_provider"], "model": row["baseline_model"]},
            "candidate": {"provider": row["candidate_provider"], "model": row["candidate_model"]},
            "perCallTimeoutSeconds": row["per_call_timeout_seconds"],
            "totalBudgetSeconds": row["total_budget_seconds"],
            "maxOutputTokens": row["max_output_tokens"],
            "status": row["status"],
            "cancelRequested": row["cancel_requested"],
            "createdAt": row["created_at"],
            "startedAt": row["started_at"],
            "completedAt": row["completed_at"],
            "deadlineAt": row["deadline_at"],
            "error": decode_json(row["error_json"]) if row["error_json"] is not None else None,
        }

    @staticmethod
    def _case_summary(row: Any) -> dict[str, Any]:
        return {
            "caseId": row["case_id"],
            "ordinal": row["ordinal"],
            "recordVersion": row["record_version"],
            "sourceKind": row["source_kind"],
            "sourceFingerprint": row["source_fingerprint"],
            "inputFingerprint": row["input_fingerprint"],
            "outputFingerprint": row["output_fingerprint"],
            "snapshot": decode_json(row["snapshot_json"]),
            "armAModelSlot": row["arm_a_model_slot"],
            "armBModelSlot": row["arm_b_model_slot"],
        }

    @staticmethod
    def _result_summary(row: Any) -> dict[str, Any]:
        return {
            "arm": row["arm"],
            "status": row["status"],
            "requestedProvider": row["requested_provider"],
            "requestedModel": row["requested_model"],
            "actualProvider": row["actual_provider"],
            "actualModel": row["actual_model"],
            "output": decode_json(row["output_json"]) if row["output_json"] is not None else None,
            "durationMs": row["duration_ms"],
            "promptTokens": row["prompt_tokens"],
            "outputTokens": row["output_tokens"],
            "cost": row["cost"],
            "error": decode_json(row["error_json"]) if row["error_json"] is not None else None,
            "startedAt": row["started_at"],
            "completedAt": row["completed_at"],
        }

    @staticmethod
    def _rating_summary(row: Any) -> dict[str, Any]:
        return {
            "ratingId": row["rating_id"],
            "runId": row["run_id"],
            "caseId": row["case_id"],
            "actorId": row["actor_id"],
            "actorKind": row["actor_kind"],
            "identityVerified": row["identity_verified"],
            "displayName": row["display_name"],
            "aPass": row["a_pass"],
            "bPass": row["b_pass"],
            "preferred": row["preferred"],
            "rationale": row["rationale"],
            "createdAt": row["created_at"],
        }


__all__ = [
    "QualityReviewRunsStore",
    "quality_review_runs_metadata",
    "quality_review_run_control",
    "quality_review_runs",
    "quality_review_run_cases",
    "quality_review_run_results",
    "quality_review_run_ratings",
]
