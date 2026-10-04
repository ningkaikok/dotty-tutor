"""Provider-independent runtime audit contracts.

这些对象只保存配置身份和版本摘要，不保存 prompt 正文、学生输入或模型响应。
因此它们可以直接嵌入既有 ``RunSnapshot.config``，而不引入新的运行框架。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

VALIDATOR_VERSION = "p0-v4"


def _digest(value: Any) -> str:
    if isinstance(value, str):
        encoded = value.encode("utf-8")
    else:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


@dataclass(frozen=True)
class PromptParts:
    """按"跨轮是否变化"切成两段的提示词。

    ``stable`` 是系统规则、题目上下文、教学脚本和输出要求——同一道题的多轮陪练里
    逐字不变；``dynamic`` 是本轮的提示层级、学生输入、交互结果和最近对话摘要。

    为什么用一个值对象而不是"再传一个 stable_prefix 字符串"：Prefix Cache 只有在
    稳定段是整段提示词的**字面前缀**时才可能命中。让 ``text`` 由 stable + dynamic
    拼出来，这个前提就是结构性的，不需要在调用边界上校验，也不可能因为有人调整
    拼接顺序而悄悄失效——而"某个字段被静默算错"正是这张指标表上出现过的问题。

    这里只做切分与度量，**不启用任何缓存**：是否启用要等真实占比数据，且只在
    Provider 明确支持时才做。
    """

    stable: str
    dynamic: str

    @property
    def text(self) -> str:
        return self.stable + self.dynamic


@dataclass(frozen=True)
class RuntimeConfigSnapshot:
    """一次 Runtime 调用的可审计配置快照。

    ``prompt`` 和 ``schema`` 是内容摘要而非正文，既能区分调用版本，又避免把
    教材内容写入审计表。``runtime`` 用于区分 generation/review/ocr/tutor。
    """

    provider: str
    model: str | None = None
    runtime: str = "runtime"
    schema: str | None = None
    prompt: str | None = None
    validator: str = VALIDATOR_VERSION
    timeout: float | None = None

    @classmethod
    def for_model(
        cls,
        provider: str,
        model: str | None,
        *,
        schema: Any = None,
        prompt: str | None = None,
        runtime: str = "generation",
        timeout: float | None = None,
    ) -> "RuntimeConfigSnapshot":
        return cls(
            provider=provider,
            model=model,
            runtime=runtime,
            schema=_digest(schema) if schema is not None else None,
            prompt=_digest(prompt) if prompt is not None else None,
            timeout=timeout,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None, **defaults: Any) -> "RuntimeConfigSnapshot":
        """Build a snapshot from a provider result and optional runtime defaults."""
        source = dict(value or {})
        return cls(
            provider=str(source.get("provider") or defaults.get("provider") or "unknown"),
            model=source.get("model", defaults.get("model")),
            runtime=str(source.get("runtime") or defaults.get("runtime") or "runtime"),
            schema=source.get("schema") or defaults.get("schema"),
            prompt=source.get("prompt") or defaults.get("prompt"),
            validator=str(source.get("validator") or defaults.get("validator") or VALIDATOR_VERSION),
            timeout=source.get("timeout", defaults.get("timeout")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "provider": self.provider,
                "model": self.model,
                "runtime": self.runtime,
                "schema": self.schema,
                "prompt": self.prompt,
                "validator": self.validator,
                "timeout": self.timeout,
            }.items()
            if value is not None
        }


@dataclass(frozen=True)
class ModelRequest:
    """Provider-independent policy for one structured model call.

    The request intentionally contains no prompt or source content.  It is safe
    to attach to logs and run snapshots while the provider adapter receives the
    prompt and JSON Schema separately.
    """

    task: str
    provider: str
    model: str
    timeout: float
    allow_fallback: bool
    schema_version: str
    runtime: str = "generation"

    def __post_init__(self) -> None:
        for field_name in ("task", "provider", "model", "schema_version", "runtime"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"ModelRequest.{field_name} 不能为空")
        if self.timeout <= 0:
            raise ValueError("ModelRequest.timeout 必须大于 0")
        if not isinstance(self.allow_fallback, bool):
            raise ValueError("ModelRequest.allow_fallback 必须是布尔值")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "provider": self.provider,
            "model": self.model,
            "timeout": self.timeout,
            "allowFallback": self.allow_fallback,
            "schemaVersion": self.schema_version,
            "runtime": self.runtime,
        }


@dataclass(frozen=True)
class ModelResult:
    """Normalized result metadata for one structured model call.

    ``output`` stays available to the immediate caller but is deliberately
    omitted from ``to_run`` so model responses are not duplicated into audit
    records.  ``actual_provider`` and ``actual_model`` make any future fallback
    visible instead of letting callers infer it from the requested values.
    """

    output: dict[str, Any] | None
    actual_provider: str
    actual_model: str
    duration_ms: float
    error: dict[str, str] | None
    usage: dict[str, int | None]
    fallback: bool = False
    fallback_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.actual_provider.strip() or not self.actual_model.strip():
            raise ValueError("ModelResult 必须记录实际 Provider 和 Model")
        if self.duration_ms < 0:
            raise ValueError("ModelResult.duration_ms 不能小于 0")
        if (self.output is None) == (self.error is None):
            raise ValueError("ModelResult 必须且只能包含 output 或 error")
        if self.fallback and not str(self.fallback_reason or "").strip():
            raise ValueError("ModelResult 回退时必须记录 fallback_reason")

    @classmethod
    def succeeded(
        cls,
        output: dict[str, Any],
        *,
        actual_provider: str,
        actual_model: str,
        duration_ms: float,
        usage: Mapping[str, int | None] | None = None,
        fallback: bool = False,
        fallback_reason: str | None = None,
    ) -> "ModelResult":
        return cls(
            output=output,
            actual_provider=actual_provider,
            actual_model=actual_model,
            duration_ms=duration_ms,
            error=None,
            usage=dict(usage or {}),
            fallback=fallback,
            fallback_reason=fallback_reason,
        )

    @classmethod
    def failed(
        cls,
        error: Exception,
        *,
        actual_provider: str,
        actual_model: str,
        duration_ms: float,
        fallback: bool = False,
        fallback_reason: str | None = None,
    ) -> "ModelResult":
        return cls(
            output=None,
            actual_provider=actual_provider,
            actual_model=actual_model,
            duration_ms=duration_ms,
            error={"type": type(error).__name__, "message": str(error)[:500]},
            usage={},
            fallback=fallback,
            fallback_reason=fallback_reason,
        )

    def metadata(self) -> dict[str, Any]:
        """Return the content-free portion that may be persisted or logged."""
        return {
            "actualProvider": self.actual_provider,
            "actualModel": self.actual_model,
            "durationMs": self.duration_ms,
            "error": dict(self.error) if self.error else None,
            "usage": dict(self.usage),
            "fallback": self.fallback,
            "fallbackReason": self.fallback_reason,
        }

    def to_run(
        self,
        request: ModelRequest,
        *,
        prompt_chars: int,
        max_output_tokens: int,
        provider_attempts: int,
        schema_fallback: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Adapt the contracts to the existing ``modelRun`` response shape."""
        metadata = self.metadata()
        run = {
            "requestedProvider": request.provider,
            "requestedModel": request.model,
            "provider": self.actual_provider,
            "model": self.actual_model,
            "actualProvider": self.actual_provider,
            "actualModel": self.actual_model,
            "fallback": self.fallback,
            "fallbackReason": self.fallback_reason,
            "promptChars": prompt_chars,
            "maxOutputTokens": max_output_tokens,
            "durationMs": self.duration_ms,
            "usage": dict(self.usage),
            "providerAttempts": provider_attempts,
            "schemaFallback": dict(schema_fallback),
            "modelRequest": request.to_dict(),
            "modelResult": metadata,
        }
        if self.error:
            # Preserve the legacy top-level string while the nested contract
            # exposes a structured type/message pair.
            run["error"] = self.error["message"]
        return run


class RuntimeExecutionError(RuntimeError):
    """模型/OCR 执行失败，同时携带冻结的配置快照。"""

    def __init__(self, message: str, *, snapshot: RuntimeConfigSnapshot, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.snapshot = snapshot
        self.cause = cause
        # Provider adapters may attach a content-free execution summary so callers
        # can report failed attempts without exposing prompts or model responses.
        self.runtime_run: dict[str, Any] | None = None

    def as_run(self) -> dict[str, Any]:
        run = {"config": self.snapshot.to_dict(), "error": str(self)[:500]}
        if self.runtime_run:
            run.update(self.runtime_run)
        return run


def attach_runtime_config(run: dict[str, Any], snapshot: RuntimeConfigSnapshot) -> dict[str, Any]:
    """Attach the standard configuration snapshot to a provider result."""
    config = snapshot.to_dict()
    run["config"] = config
    run["runtimeConfig"] = config
    return run


__all__ = [
    "ModelRequest",
    "ModelResult",
    "PromptParts",
    "RuntimeConfigSnapshot",
    "RuntimeExecutionError",
    "VALIDATOR_VERSION",
    "attach_runtime_config",
]
