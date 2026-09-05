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
    "PromptParts",
    "RuntimeConfigSnapshot",
    "RuntimeExecutionError",
    "VALIDATOR_VERSION",
    "attach_runtime_config",
]
