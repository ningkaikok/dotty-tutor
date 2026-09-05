"""模型调用边界指标的持久化与聚合（roadmap T2）。

设计约束：
- 只追加写入（append-only）：每次模型调用一行，失败也记录——这是"回退率/
  失败率可解释"的前提；不做更新或删除。
- 聚合是只读查询，按 runtime/task/provider/model 分组输出调用数、失败数、
  平均耗时与 token 合计；分母为零的组自然不会出现。
- token 维度目前仅 Ollama 提供原生计数；Codex CLI 路径记 None，
  不用字符数冒充 token。
- 提示词稳定段/动态段按**字符数**记录，不换算成 token：Provider 只回报整段提示词的
  token 总数，按字符比例摊回去是估算而不是测量。占比本身用字符算已经够判断
  Prefix Cache 值不值得做，没必要在库里存一个看起来精确的估算值。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
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
    # 回退信息。一行 = 一次逻辑调用，provider_attempts 是这次逻辑调用真正打给
    # Provider 的请求数（含重试），因此"逻辑调用数"和"Provider 请求数"必须分开
    # 统计——这与 judge 报告里既有的 logicalCalls / providerAttempts 口径一致。
    Column("provider_attempts", Integer, nullable=False, default=1),
    # 约束解码不可用时降级为普通 JSON 模式。降级率是判断模型/Provider 能力是否
    # 达标的直接信号，不能和普通失败混为一谈。
    Column("schema_fallback", Boolean, nullable=False, default=False),
    # 提示词的稳定段/动态段字符数（陪练路径按 PromptParts 切分后写入）。
    # 两列都**可空**：NULL 表示这次调用没有做切分（不适用），与"稳定段长度为 0"
    # 是两种状态。用 0 回填会把没测过的调用算进分母，直接压低稳定占比，
    # 而稳定占比正是判断 Prefix Cache 值不值得做的唯一依据。
    Column("stable_prompt_chars", Integer),
    Column("dynamic_prompt_chars", Integer),
)

# 做过前缀切分的行：两列都非空。任一为空都说明这次调用没有切分，不能进入占比分母。
_prefix_split = and_(
    model_call_metrics.c.stable_prompt_chars.is_not(None),
    model_call_metrics.c.dynamic_prompt_chars.is_not(None),
)

_ALLOWED_KEYS = {
    "runtime", "task", "provider", "model", "duration_ms", "prompt_chars",
    "max_output_tokens", "prompt_tokens", "output_tokens", "status", "error_type",
    "provider_attempts", "schema_fallback",
    "stable_prompt_chars", "dynamic_prompt_chars",
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
        """Keep store call sites uniform; schema creation is Alembic-owned."""
        return None

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
            func.coalesce(func.sum(model_call_metrics.c.provider_attempts), 0).label("provider_attempts"),
            func.sum(case((model_call_metrics.c.schema_fallback, 1), else_=0)).label("schema_fallbacks"),
            func.sum(model_call_metrics.c.stable_prompt_chars).label("stable_prompt_chars"),
            func.sum(model_call_metrics.c.dynamic_prompt_chars).label("dynamic_prompt_chars"),
            func.sum(case((_prefix_split, 1), else_=0)).label("prefix_split_calls"),
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
                "providerAttempts": int(row["provider_attempts"] or 0),
                "schemaFallbacks": int(row["schema_fallbacks"] or 0),
                # 只在做过切分的调用上有值；未切分的分组三项都是 None / 0。
                "prefixSplitCalls": int(row["prefix_split_calls"] or 0),
                "stablePromptChars": _integer_or_none(row["stable_prompt_chars"]),
                "dynamicPromptChars": _integer_or_none(row["dynamic_prompt_chars"]),
                "stablePromptShare": _share(
                    row["stable_prompt_chars"], row["dynamic_prompt_chars"]
                ),
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
            func.sum(model_call_metrics.c.provider_attempts).label("provider_attempts"),
            func.sum(case((model_call_metrics.c.schema_fallback, 1), else_=0)).label("schema_fallbacks"),
            func.sum(model_call_metrics.c.stable_prompt_chars).label("stable_prompt_chars"),
            func.sum(model_call_metrics.c.dynamic_prompt_chars).label("dynamic_prompt_chars"),
            func.sum(case((_prefix_split, 1), else_=0)).label("prefix_split_calls"),
        ).select_from(model_call_metrics)
        if conditions:
            statement = statement.where(*conditions)
        with self.engine.connect() as connection:
            row = connection.execute(statement).mappings().one()

        logical_calls = int(row["logical_calls"] or 0)
        failures = int(row["failures"] or 0)
        token_measured_calls = int(row["token_measured_calls"] or 0)
        provider_attempts = int(row["provider_attempts"] or 0)
        schema_fallbacks = int(row["schema_fallbacks"] or 0)
        prefix_split_calls = int(row["prefix_split_calls"] or 0)
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
                # Provider 请求数与逻辑调用数分开：重试会让前者大于后者，
                # 二者的比值就是重试放大倍数，也是"是否该换 Provider"的直接依据。
                "providerAttempts": provider_attempts,
                "retryAmplification": _ratio(provider_attempts, logical_calls),
                "schemaFallbacks": schema_fallbacks,
                "schemaFallbackRate": _ratio(schema_fallbacks, logical_calls),
                # Prefix Cache 判据：稳定段占提示词的比例。只统计做过切分的调用，
                # 覆盖率单独给出——占比再高，如果只覆盖了极少数调用也不构成依据。
                "prefixSplitCalls": prefix_split_calls,
                "prefixSplitCoverageRate": _ratio(prefix_split_calls, logical_calls),
                "stablePromptChars": _integer_or_none(row["stable_prompt_chars"]),
                "dynamicPromptChars": _integer_or_none(row["dynamic_prompt_chars"]),
                "stablePromptShare": _share(
                    row["stable_prompt_chars"], row["dynamic_prompt_chars"]
                ),
            },
            "items": self.aggregate(days=days),
        }


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 3)


def _share(stable: Any, dynamic: Any) -> float | None:
    """稳定段占整段提示词的比例；没有切分过的调用返回 None 而不是 0。"""
    if stable is None or dynamic is None:
        return None
    total = int(stable) + int(dynamic)
    if total <= 0:
        return None
    return round(int(stable) / total, 3)


def _number_or_none(value: Any) -> float | None:
    return round(float(value), 1) if value is not None else None


def _integer_or_none(value: Any) -> int | None:
    return int(value) if value is not None else None
