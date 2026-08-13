"""Deterministic teaching plans for one tutoring turn.

The plan is deliberately independent from model prompts and persistence.  It
turns facts already decided by the system into a small auditable teaching
contract, so a generated sentence cannot silently change assessment or stage.
"""

from __future__ import annotations

from typing import Any


ERROR_STRATEGIES = {
    "concept": ("concept-foundation", "先验证基础概念，再应用到相邻情境"),
    "reading": ("condition-reading", "改变题面表达，重点训练提取条件和问题目标"),
    "calculation": ("parallel-calculation", "保持计算结构但替换数值，要求展示关键计算"),
    "missing_step": ("step-completion", "保留推理主线，设置需要补全的关键步骤"),
    "unknown": ("scaffolded-transfer", "从更基础的同知识点题开始，再逐步迁移"),
    "careless": ("self-check", "加入结果、单位和条件的自检步骤"),
}


def teaching_strategy_context(error_reason: str | None, current_stage: str) -> str:
    """Return a short strategy constraint safe to include in a model prompt.

    这里只传递学生已经确认的错误原因和本轮教学目标，不包含内部提示词或
    学生历史原文。模型负责把策略说自然，不能借此修改判题和阶段。
    """
    reason = error_reason if error_reason in ERROR_STRATEGIES else "unknown"
    strategy, objective = ERROR_STRATEGIES[reason]
    return (
        f"本轮教学策略：{strategy}（{objective}）。"
        f"当前阶段：{current_stage}。只推进一个最小教学动作，不重复上一条提示。"
    )


def suggested_stage(
    current_stage: str,
    assessment: str,
    mode: str,
    *,
    assessment_authority: str = "guided",
) -> str:
    """Return the only allowed state transition for a tutor turn.

    这是领域规则而非模型建议：即使生成模型措辞过度乐观，也只能使用已传入的
    assessment，且永远不能把线程推进到“已掌握”。
    """
    # 只有确定性答案引擎能凭“正确”推进流程。模型对自由文字的正向判断只
    # 作为辅导线索，否则一句过度乐观的生成文本就可能跳过练习或验证。
    if assessment == "correct" and assessment_authority == "deterministic":
        return {
            "diagnose": "practice",
            "explain": "practice",
            "practice": "verify",
            "verify": "verify",
        }.get(current_stage, "explain")
    if current_stage == "diagnose" or assessment == "incorrect":
        return "explain"
    return current_stage if mode == "help" else "explain"


def build_tutor_turn_plan(
    *,
    error_reason: str | None,
    current_stage: str,
    mode: str,
    assessment: str,
    reply_source: str,
    assessment_authority: str = "guided",
) -> dict[str, Any]:
    """Build a stable, JSON-safe plan from confirmed and deterministic facts."""
    reason = error_reason if error_reason in ERROR_STRATEGIES else "unknown"
    strategy, objective = ERROR_STRATEGIES[reason]
    authority = "deterministic" if assessment_authority == "deterministic" else "guided"
    next_stage = suggested_stage(
        current_stage,
        assessment,
        mode,
        assessment_authority=authority,
    )
    intent_by_stage = {
        "diagnose": "定位学生当前卡点",
        "explain": "纠正已确认的误区",
        "practice": "引导学生完成下一步练习",
        "verify": "验证学生能否说明依据",
    }
    action_by_stage = {
        "diagnose": "ask-for-reasoning",
        "explain": "targeted-hint",
        "practice": "guided-practice",
        "verify": "ask-for-justification",
    }
    return {
        "intent": intent_by_stage.get(current_stage, intent_by_stage["explain"]),
        "assessment": assessment,
        "errorStrategy": {"id": strategy, "objective": objective, "reason": reason},
        "teachingAction": action_by_stage.get(current_stage, action_by_stage["explain"]),
        # 仅确定性判题路径可显示标准答案；普通提示不能借计划越权泄题。
        "shouldRevealAnswer": mode == "answer" and authority == "deterministic" and assessment == "incorrect",
        "suggestedStage": next_stage,
        "replySource": reply_source,
        "audit": {
            "assessmentAuthority": authority,
            "stageAuthority": "tutor-turn-plan",
            "modelMayOverride": False,
        },
    }
