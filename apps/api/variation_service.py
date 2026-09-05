"""从已确认的错题生成聚焦式变式练习。

两段式设计：先按错误原因查 ``ERROR_STRATEGIES`` 选定教学策略（概念混淆练辨析、
计算失误练运算），再把策略和约束写进提示词委托结构化生成。策略先行是为了让
变式题服务诊断结论，而不是让模型自由发挥出新的一道无关题。

硬门禁在 generate 入口：变式题必须落在可确定性判题的题型内（与
domain.questions.pipeline.DETERMINISTIC_ANSWER_TYPES 保持一致），否则拒绝——验证题
要靠确定性判题推进掌握状态，模型不能既出题又判题。模型偶尔会答非所问（如默认出主观
题型），因此校验失败时把错误反馈进提示词重试一次，避免用同一个必然失败的提示词
把整段陪练卡死；仍不合格才向上抛出交给路由层转成 4xx。
"""

from __future__ import annotations

from typing import Any, Callable

from domain.questions.pipeline import build_question_content_blocks
from domain.tutoring.turn_plan import ERROR_STRATEGIES, resolve_error_strategy

VariationGenerator = Callable[[str], tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]]

LEVELS = ("foundation", "parallel", "transfer")
# 必须与 domain.questions.pipeline.DETERMINISTIC_ANSWER_TYPES 保持一致，
# 否则确定性判题器已支持的题型（如 true-false）会在这里被误判为不合规。
SUPPORTED_QUESTION_TYPES = {"choice", "multi-select", "fill-blank", "numeric", "true-false"}
STRATEGY_VERSION = "variation-strategy-v1"
GENERATION_ATTEMPTS = 2


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
        base_prompt = (
            "你正在为学生生成一道用于掌握验证的数学变式题。\n"
            f"学段：{mistake.get('gradeBand', '初中')}\n"
            f"章节：{mistake.get('chapter', '')}\n"
            f"知识点：{mistake.get('knowledgePoint', '')}\n"
            f"原题：{original.get('prompt', '')}\n"
            f"学生错误原因：{reason}\n"
            f"教学策略：{objective}\n"
            f"难度层级：{level}\n"
            "要求：不得复制原题；改变数字、语境或设问，但只考查同一知识点。"
            "题型必须是 choice、multi-select、fill-blank、numeric 或 true-false 之一，"
            "以便系统确定性判题，不得生成简答题等主观题型。"
            "答案字段必须完整，讲解步骤只解释新题。"
        )
        payload: dict[str, Any] = {}
        question: dict[str, Any] = {}
        guide_cards: list[dict[str, Any]] = []
        model_run: dict[str, Any] = {}
        error: ValueError | None = None
        for attempt in range(1, GENERATION_ATTEMPTS + 1):
            prompt = base_prompt
            if error is not None:
                prompt += f"\n上一次生成不合格：{error}。请修正后重新生成一道新题，不要重复同样的问题。"
            payload, guide_cards, model_run = self.generator(prompt)
            question = payload.setdefault("question", {})
            if question.get("questionType") not in SUPPORTED_QUESTION_TYPES:
                error = ValueError("变式题必须使用可确定判题的题型")
            elif not question.get("prompt") or question.get("prompt") == original.get("prompt"):
                error = ValueError("变式题不能缺少题干或直接复制原题")
            else:
                error = None
                break
        if error is not None:
            raise error
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
        # generate_lesson() 只在走 OCR 识别路径时才会拿到调用方拼装的 contentBlocks
        # （见 mistake_recognition.py）；变式题没有源图片，这里直接用同一套构建函数，
        # 否则学生端渲染会因为 contentBlocks 缺失而崩溃。
        question["contentBlocks"] = build_question_content_blocks(payload, question.get("prompt", ""), [])
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
