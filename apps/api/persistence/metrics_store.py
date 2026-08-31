"""模型调用边界指标的持久化与聚合（roadmap T2）。

设计约束：
- 只追加写入（append-only）：每次模型调用一行，失败也记录——这是"回退率/
  失败率可解释"的前提；不做更新或删除。
- 聚合是只读查询，按 runtime/task/provider/model 分组输出调用数、失败数、
  平均耗时与 token 合计；分母为零的组自然不会出现。
- token 维度目前仅 Ollama 提供原生计数；Codex CLI 路径记 None，
  不用字符数冒充 token。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import (
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    and_,
    case,
    func,
    select,
)
from sqlalchemy.engine import Engine

metadata = MetaData()

model_call_metrics = Table(
    "model_call_metrics",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("created_at", Float, nullable=False),
    Column("runtime", String(16), nullable=False),
    Column("task", String(48), nullable=False),
    Column("provider", String(32), nullable=False),
    Column("model", String(96), nullable=False),
    Column("duration_ms", Float, nullable=False),
    Column("prompt_chars", Integer, nullable=False, default=0),
    Column("max_output_tokens", Integer),
    Column("prompt_tokens", Integer),
    Column("output_tokens", Integer),
    Column("status", String(16), nullable=False),
    Column("error_type", String(64)),
)

_ALLOWED_KEYS = {
    "runtime", "task", "provider", "model", "duration_ms", "prompt_chars",
    "max_output_tokens", "prompt_tokens", "output_tokens", "status", "error_type",
}


class MetricsStore:
    """惰性建表：与其他 Store 一致，构造时不触碰数据库。

    这一点至关重要——组合根在 import 阶段就会创建本 Store（供 OpenAPI 导出等
    无数据库场景复用）；若在 ``__init__`` 里建表，CI 的类型检查步骤会因为
    尝试连接 PostgreSQL 而失败。
    """

    def __init__(self, *, engine: Engine) -> None:
        self.engine = engine

    def _ensure_initialized(self) -> None:
        from persistence.schema_registry import initialize_sqlite_schema

        if self.engine.dialect.name == "sqlite":
            initialize_sqlite_schema(self.engine)

    def record(self, entry: dict[str, Any]) -> None:
        """追加一条调用记录；白名单外字段直接忽略，避免脏数据进入聚合。"""
        self._ensure_initialized()
        values = {key: entry[key] for key in _ALLOWED_KEYS if key in entry}
        values.setdefault(
            "id", uuid.uuid4().hex,
        )
        values.setdefault("created_at", time.time())
        with self.engine.begin() as connection:
            connection.execute(model_call_metrics.insert().values(**values))

    def aggregate(self, *, days: int | None = None) -> list[dict[str, Any]]:
        """按 runtime/task/provider/model 分组的调用量与成本代理指标。"""
        self._ensure_initialized()
        conditions = []
        if days is not None and days > 0:
            cutoff = time.time() - days * 86400
            conditions.append(model_call_metrics.c.created_at >= cutoff)
        grouping = [
            model_call_metrics.c.runtime,
            model_call_metrics.c.task,
            model_call_metrics.c.provider,
            model_call_metrics.c.model,
        ]
        statement = select(
            *grouping,
            func.count().label("calls"),
            func.sum(case((model_call_metrics.c.status == "failed", 1), else_=0)).label("failures"),
            func.avg(model_call_metrics.c.duration_ms).label("avg_duration_ms"),
            func.coalesce(func.sum(model_call_metrics.c.output_tokens), 0).label("outputTokens"),
        ).select_from(model_call_metrics)
        if conditions:
            statement = statement.where(*conditions)
        statement = statement.group_by(*grouping).order_by(grouping[0], grouping[1])
        with self.engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        return [
            {
                "runtime": row["runtime"],
                "task": row["task"],
                "provider": row["provider"],
                "model": row["model"],
                "calls": row["calls"],
                "failures": int(row["failures"] or 0),
                "avgDurationMs": round(float(row["avg_duration_ms"] or 0), 1),
                "totalOutputTokens": int(row["outputTokens"] or 0),
            }
            for row in rows
        ]

    def aggregate_report(self, *, days: int | None = None) -> dict[str, Any]:
        """Return report-level model-call metrics without inventing money costs.

        ``model_call_metrics`` is a logical runtime-call ledger.  A missing token
        value means that the provider did not expose usage, not that the call used
        zero tokens, so totals and coverage are kept explicit in the response.
        """
        self._ensure_initialized()
        conditions = []
        if days is not None and days > 0:
            cutoff = time.time() - days * 86400
            conditions.append(model_call_metrics.c.created_at >= cutoff)
        token_measured = and_(
            model_call_metrics.c.prompt_tokens.is_not(None),
            model_call_metrics.c.output_tokens.is_not(None),
        )
        statement = select(
            func.count().label("logical_calls"),
            func.sum(case((model_call_metrics.c.status == "failed", 1), else_=0)).label("failures"),
            func.avg(model_call_metrics.c.duration_ms).label("avg_duration_ms"),
            func.sum(model_call_metrics.c.prompt_tokens).label("prompt_tokens"),
            func.sum(model_call_metrics.c.output_tokens).label("output_tokens"),
            func.sum(case((token_measured, 1), else_=0)).label("token_measured_calls"),
        ).select_from(model_call_metrics)
        if conditions:
            statement = statement.where(*conditions)
        with self.engine.connect() as connection:
            row = connection.execute(statement).mappings().one()

        logical_calls = int(row["logical_calls"] or 0)
        failures = int(row["failures"] or 0)
        token_measured_calls = int(row["token_measured_calls"] or 0)
        return {
            "summary": {
                "logicalCalls": logical_calls,
                "failures": failures,
                "failureRate": _ratio(failures, logical_calls),
                "avgDurationMs": _number_or_none(row["avg_duration_ms"]),
                "totalPromptTokens": _integer_or_none(row["prompt_tokens"]),
                "totalOutputTokens": _integer_or_none(row["output_tokens"]),
                "tokenMeasuredCalls": token_measured_calls,
                "tokenCoverageRate": _ratio(token_measured_calls, logical_calls),
            },
            "items": self.aggregate(days=days),
        }


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 3)


def _number_or_none(value: Any) -> float | None:
    return round(float(value), 1) if value is not None else None


def _integer_or_none(value: Any) -> int | None:
    return int(value) if value is not None else None
