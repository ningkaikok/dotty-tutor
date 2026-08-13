"""Generate a focused practice variation from a confirmed mistake."""

from __future__ import annotations

from typing import Any, Callable

from tutor_turn_plan import ERROR_STRATEGIES


VariationGenerator = Callable[[str], tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]]

LEVELS = ("foundation", "parallel", "transfer")
SUPPORTED_QUESTION_TYPES = {"choice", "multi-select", "fill-blank", "numeric"}


class VariationService:
    """Choose a pedagogical strategy, then delegate structured generation."""

    def __init__(self, *, generator: VariationGenerator) -> None:
        self.generator = generator

    def generate(self, mistake: dict[str, Any], sequence: int) -> dict[str, Any]:
        reason = mistake.get("errorReason") or "unknown"
        strategy, objective = ERROR_STRATEGIES.get(reason, ERROR_STRATEGIES["unknown"])
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
        question["variationLevel"] = level
        return {
            "strategy": strategy,
            "level": level,
            "questionPayload": payload,
            "guideCards": guide_cards,
            "modelRun": model_run,
        }
