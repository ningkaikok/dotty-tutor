"""模型能力目录与健康记录。

roadmap T2/Q7 要求按"任务能力"筛选候选模型，而不是把供应商名称原样抛给界面；
同时提供轻量健康状态（连续失败计数 + 最近一次失败原因），供候选筛选参考。

三条硬边界：
1. **健康状态只影响候选筛选**——绝不覆盖已开始运行的 ``RunSnapshot``，也绝不
   修改当前选择；一次调用的实际 provider/model 以快照为准。
2. **未知模型不猜测**。Ollama 的 tag 是动态的（用户随时可以拉新模型），目录里
   没有精确/前缀匹配的条目时返回保守默认值（能力标签为空、上下文上限记 0 表
   示"未声明"），宁可少声明也不编造规格。
3. **角色过滤是服务端职责**。学生端只应看到产品允许的陪练模型选项；本模块提
   供 :func:`eligible_for_role`，由路由层决定暴露哪些候选。

上下文上限只填写公开可查的确定值；不确定的一律记 0。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

ROLES = frozenset({"generation", "review", "tutoring", "vision"})
CAPABILITY_TAGS = frozenset({"json-schema", "vision", "math", "long-context"})


@dataclass(frozen=True)
class ModelCapability:
    """单个模型条目的静态能力声明。"""

    provider: str
    # 精确模型名，或以 "*" 结尾的前缀通配（Ollama tag 动态变化，如 "qwen2.5:*"）。
    model_pattern: str
    display_name: str
    roles: frozenset[str]
    capabilities: frozenset[str]
    context_window: int  # token 上限；0 = 未声明
    latency_tier: str  # fast | moderate | slow
    cost_tier: str  # local-free | subscription | free
    fallback: tuple[str, str] | None  # 建议的回退 (provider, model)


_MODEL_CAPABILITIES: tuple[ModelCapability, ...] = (
    ModelCapability(
        "ollama", "deepseek-r1:*", "DeepSeek-R1 推理系列",
        frozenset({"generation", "review", "tutoring"}),
        frozenset({"json-schema", "math"}),
        8192, "slow", "local-free", ("ollama", "qwen2.5:7b"),
    ),
    ModelCapability(
        "ollama", "qwen2.5:*", "Qwen2.5 通用系列",
        frozenset({"generation", "review", "tutoring"}),
        frozenset({"json-schema"}),
        32768, "moderate", "local-free", ("codex", "default"),
    ),
    ModelCapability(
        "codex", "gpt-5.6-sol", "Codex gpt-5.6-sol",
        frozenset({"generation", "review", "tutoring", "vision"}),
        frozenset({"json-schema", "vision", "math", "long-context"}),
        0, "moderate", "subscription", ("codex", "gpt-5.6-luna"),
    ),
    ModelCapability(
        "codex", "gpt-5.6-luna", "Codex gpt-5.6-luna",
        frozenset({"generation", "review", "tutoring", "vision"}),
        frozenset({"json-schema", "vision", "math", "long-context"}),
        0, "fast", "subscription", ("codex", "gpt-5.5"),
    ),
    ModelCapability(
        "codex", "gpt-5.6-terra", "Codex gpt-5.6-terra",
        frozenset({"generation", "review", "tutoring", "vision"}),
        frozenset({"json-schema", "vision", "long-context"}),
        0, "slow", "subscription", ("codex", "gpt-5.5"),
    ),
    ModelCapability(
        "codex", "default", "Codex 默认模型",
        frozenset({"generation", "review", "tutoring", "vision"}),
        frozenset({"json-schema", "vision", "math", "long-context"}),
        0, "moderate", "subscription", ("mock", "static-demo"),
    ),
    ModelCapability(
        "codex", "*", "Codex 订阅模型",
        frozenset({"generation", "review", "tutoring", "vision"}),
        frozenset({"json-schema", "vision", "long-context"}),
        0, "moderate", "subscription", ("codex", "default"),
    ),
    ModelCapability(
        "mock", "static-demo", "Mock 固定模式",
        frozenset({"generation", "review", "tutoring", "vision"}),
        frozenset({"json-schema"}),
        0, "fast", "free", None,
    ),
)


def capability_for(provider: str, model: str) -> ModelCapability:
    """查找模型能力：精确名 → 最长前缀通配 → 保守默认。

    保守默认不声明任何能力标签、不设回退，避免把未登记的模型误标成
    "支持视觉/长上下文"。它仍然出现在所有角色的候选里，由调用方决定取舍。
    """
    exact = [item for item in _MODEL_CAPABILITIES if item.provider == provider and item.model_pattern == model]
    if exact:
        return exact[0]
    prefixed = [
        item for item in _MODEL_CAPABILITIES
        if item.provider == provider and item.model_pattern.endswith("*")
        and model.startswith(item.model_pattern[:-1])
    ]
    if prefixed:
        return max(prefixed, key=lambda item: len(item.model_pattern))
    return ModelCapability(
        provider=provider,
        model_pattern=model,
        display_name=model or "(未命名)",
        roles=frozenset(ROLES),
        capabilities=frozenset(),
        context_window=0,
        latency_tier="unspecified",
        cost_tier="unspecified",
        fallback=None,
    )


class ModelHealthBook:
    """进程内的轻量健康记录。

    只记录连续失败次数和最近一次失败原因：达到阈值即视为不健康，候选筛选时
    把它排到健康模型之后或直接过滤。任何成功调用都会清零——健康是筛选提示，
    不是封禁；历史运行的真实 provider/model 永远以 ``RunSnapshot`` 为准。
    """

    FAILURE_THRESHOLD = 3

    def __init__(self) -> None:
        self._consecutive_failures: dict[tuple[str, str], int] = {}
        self._last_failure: dict[tuple[str, str], dict[str, Any]] = {}

    def mark_success(self, provider: str, model: str) -> None:
        self._consecutive_failures[(provider, model)] = 0

    def mark_failure(self, provider: str, model: str, reason: str) -> None:
        key = (provider, model)
        self._consecutive_failures[key] = self._consecutive_failures.get(key, 0) + 1
        self._last_failure[key] = {
            "reason": reason[:300],
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def snapshot(self, provider: str, model: str) -> dict[str, Any]:
        key = (provider, model)
        consecutive = self._consecutive_failures.get(key, 0)
        last = self._last_failure.get(key)
        return {
            "healthy": consecutive < self.FAILURE_THRESHOLD,
            "consecutiveFailures": consecutive,
            "lastFailureReason": last["reason"] if last else None,
            "lastFailureAt": last["at"] if last else None,
        }


HEALTH_BOOK = ModelHealthBook()


def annotate_model_entry(provider: str, model: str) -> dict[str, Any]:
    """把能力和健康信息拼成目录接口可直接使用的扁平字段。"""
    capability = capability_for(provider, model)
    return {
        "name": model,
        "displayName": capability.display_name,
        "roles": sorted(capability.roles),
        "capabilities": sorted(capability.capabilities),
        "contextWindow": capability.context_window,
        "latencyTier": capability.latency_tier,
        "costTier": capability.cost_tier,
        "fallback": f"{capability.fallback[0]}/{capability.fallback[1]}" if capability.fallback else None,
        "health": HEALTH_BOOK.snapshot(provider, model),
    }


def eligible_for_role(entry: dict[str, Any], role: str) -> bool:
    """按任务角色筛选候选：角色匹配且当前健康。

    未登记模型（roles 含全部四类）天然通过；健康状态只在这里生效，
    不影响任何已经开始的运行。
    """
    return role in entry.get("roles", []) and entry.get("health", {}).get("healthy", True)
