"""受约束 Tutor 工具提案与策略判定。

模型只能提出工具意图，不能携带任意参数或直接执行。策略层只读取服务端已经
持久化的输入、阶段和判定证据，并以 shadow 事件记录结果，方便评测越权率。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TOOL_POLICY_VERSION = "tool-policy-v1"
TOOL_NAMES = (
    "evaluate_answer",
    "explain_mistake",
    "generate_variation",
    "record_evidence",
    "schedule_review",
)
ToolName = Literal[
    "evaluate_answer",
    "explain_mistake",
    "generate_variation",
    "record_evidence",
    "schedule_review",
]


class ToolProposal(BaseModel):
    """模型可表达的最小工具意图；禁止任意参数和执行指令。"""

    model_config = ConfigDict(extra="forbid")

    name: ToolName
    reason: str = Field(min_length=1, max_length=240)
    evidenceRefs: list[str] = Field(default_factory=list, max_length=8)


class ToolPolicyDecision(BaseModel):
    name: ToolName
    decision: Literal["allow", "deny"]
    reason: str
    policyVersion: str = TOOL_POLICY_VERSION


def normalize_tool_proposals(value: Any) -> list[dict[str, Any]]:
    """丢弃不符合严格契约的模型提案，不让坏提案进入策略层。"""
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value[:5]:
        try:
            normalized.append(ToolProposal.model_validate(item).model_dump())
        except Exception:
            continue
    return normalized


def validate_tool_proposal(
    proposal: dict[str, Any],
    *,
    stage: str,
    input_item: dict[str, Any] | None,
    action: dict[str, Any],
) -> ToolPolicyDecision:
    """根据已确认事实决定是否允许提案进入下一执行层。

    当前实现永远只返回策略结果，不执行工具。所有允许条件都要求证据引用，
    这样后续接入 executor 时不会把模型生成的自由文本当作事实来源。
    """
    try:
        parsed = ToolProposal.model_validate(proposal)
    except Exception:
        name = str(proposal.get("name") or "explain_mistake")
        if name not in TOOL_NAMES:
            name = "explain_mistake"
        return ToolPolicyDecision(name=name, decision="deny", reason="提案不符合严格工具契约")

    refs = [ref.strip() for ref in parsed.evidenceRefs if ref.strip()]
    if not refs:
        return ToolPolicyDecision(name=parsed.name, decision="deny", reason="工具提案必须引用已存在的证据")

    confirmed_input = bool(input_item and input_item.get("status") == "confirmed")
    has_observable_answer = bool(
        input_item
        and (
            input_item.get("interactionResult")
            or input_item.get("formulaRecognitions")
            or input_item.get("canvasState")
            or input_item.get("content", "").strip()
        )
    )
    assessment = action.get("assessment")
    allowed = {
        "evaluate_answer": confirmed_input and has_observable_answer and stage in {"diagnose", "explain", "verify"},
        "explain_mistake": stage in {"diagnose", "explain"},
        "generate_variation": stage in {"explain", "practice", "verify"},
        "record_evidence": confirmed_input,
        "schedule_review": stage == "verify" and assessment == "correct",
    }[parsed.name]
    if not allowed:
        return ToolPolicyDecision(name=parsed.name, decision="deny", reason="当前阶段或输入状态不允许该工具")
    return ToolPolicyDecision(name=parsed.name, decision="allow", reason="提案满足阶段、确认状态和证据引用约束")
