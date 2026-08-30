"""从已确认的错题生成聚焦式变式练习。

两段式设计：先按错误原因查 ``ERROR_STRATEGIES`` 选定教学策略（概念混淆练辨析、
计算失误练运算），再把策略和约束写进提示词委托结构化生成。策略先行是为了让
变式题服务诊断结论，而不是让模型自由发挥出新的一道无关题。

硬门禁在 generate 入口：变式题必须落在可确定性判题的题型内（choice/fill-blank/
numeric 等），否则直接拒绝——验证题要靠确定性判题推进掌握状态，模型不能既出题又判题。
"""

from __future__ import annotations

from typing import Any, Callable

from domain.tutoring.turn_plan import ERROR_STRATEGIES, resolve_error_strategy

VariationGenerator = Callable[[str], tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]]

LEVELS = ("foundation", "parallel", "transfer")
SUPPORTED_QUESTION_TYPES = {"choice", "multi-select", "fill-blank", "numeric"}
STRATEGY_VERSION = "variation-strategy-v1"


class VariationService:
    """Choose a pedagogical strategy, then delegate structured generation."""

    def __init__(self, *, generator: VariationGenerator) -> None:
        self.generator = generator

    def generate(self, mistake: dict[str, Any], sequence: int) -> dict[str, Any]:
        reason, attribution_source = resolve_error_strategy(
            mistake.get("errorReason"),
            ai_error_reason=mistake.get("aiErrorReason"),
        )
        strategy, objective = ERROR_STRATEGIES[reason]
        level = LEVELS[min(max(sequence - 1, 0), len(LEVELS) - 1)]
        original = mistake["questionPayload"]["question"]
        prompt = (
            "你正在为学生生成一道用于掌握验证的数学变式题。\n"
            f"学段：{mistake.get('gradeBand', '初中')}\n"
            f"章节：{mistake.get('chapter', '')}\n"
            f"知识点：{mistake.get('knowledgePoint', '')}\n"
            f"原题：{original.get('prompt', '')}\n"
            f"学生错误原因：{reason}\n"
            f"教学策略：{objective}\n"
            f"难度层级：{level}\n"
            "要求：不得复制原题；改变数字、语境或设问，但只考查同一知识点。"
            "优先生成 choice、fill-blank 或 numeric，以便系统确定性判题。"
            "答案字段必须完整，讲解步骤只解释新题。"
        )
        payload, guide_cards, model_run = self.generator(prompt)
        question = payload.setdefault("question", {})
        if question.get("questionType") not in SUPPORTED_QUESTION_TYPES:
            raise ValueError("变式题必须使用可确定判题的题型")
        if not question.get("prompt") or question.get("prompt") == original.get("prompt"):
            raise ValueError("变式题不能缺少题干或直接复制原题")
        question["chapter"] = mistake.get("chapter", question.get("chapter", ""))
        question["knowledgePoint"] = mistake.get(
            "knowledgePoint",
            question.get("knowledgePoint", ""),
        )
        question["variationOf"] = original.get("id")
        question["variationStrategy"] = strategy
        question["variationStrategyVersion"] = STRATEGY_VERSION
        question["variationObjective"] = objective
        question["variationTarget"] = reason
        question["variationAttributionSource"] = attribution_source
        question["variationLevel"] = level
        return {
            "strategy": strategy,
            "strategyVersion": STRATEGY_VERSION,
            "target": reason,
            "attributionSource": attribution_source,
            "objective": objective,
            "level": level,
            "questionPayload": payload,
            "guideCards": guide_cards,
            "modelRun": model_run,
        }
