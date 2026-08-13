"""为单轮陪练生成可审计的教学计划。

本模块只包含确定性领域规则。模型可以组织回复并提出误区假设，但不能决定
学生意图、教学动作、答案正误或阶段迁移；集中维护这条权限边界，才能让陪练
行为稳定、可测试，也便于学习项目的维护者理解。
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any


STUDENT_INTENTS = (
    "submit-answer",
    "request-hint",
    "request-explanation",
    "check-step",
    "challenge-answer",
    "request-example",
    "express-confusion",
    "off-topic",
)

TEACHING_ACTIONS = (
    "extract-conditions",
    "inspect-first-error",
    "contrast-concepts",
    "complete-step",
    "show-micro-example",
    "ask-justification",
    "generate-micro-practice",
    "run-self-check",
)

MISCONCEPTION_CONFIDENCE_THRESHOLD = 0.65

ERROR_STRATEGIES = {
    "concept": ("concept-foundation", "先验证基础概念，再应用到相邻情境"),
    "reading": ("condition-reading", "改变题面表达，重点训练提取条件和问题目标"),
    "calculation": ("parallel-calculation", "保持计算结构但替换数值，要求展示关键计算"),
    "missing_step": ("step-completion", "保留推理主线，设置需要补全的关键步骤"),
    "unknown": ("scaffolded-transfer", "从更基础的同知识点题开始，再逐步迁移"),
    "careless": ("self-check", "加入结果、单位和条件的自检步骤"),
}


def _compact_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _has_structured_answer(value: Any) -> bool:
    """Return whether an interaction payload contains an actual student answer."""
    if isinstance(value, dict):
        return any(_has_structured_answer(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_structured_answer(item) for item in value)
    if isinstance(value, bool):
        return True
    return value is not None and bool(str(value).strip())


def infer_student_intent(
    *,
    mode: str,
    content: str,
    interaction_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """不增加模型调用，稳定识别本轮唯一学生意图。

    结构化作答最可信，其次是学生点击的操作模式。中文口语只补充两者无法
    表达的“质疑答案、请求例子、检查步骤”等语义，避免小模型每轮先做一次
    分类而增加延迟和不可重复性。
    """
    text = _compact_text(content, 1_000).lower()
    evidence: list[str] = []

    if _has_structured_answer(interaction_result or {}):
        return {
            "id": "submit-answer",
            "confidence": 0.99,
            "evidence": ["interaction-result"],
        }

    patterns: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("challenge-answer", ("答案错了", "答案不对", "标准答案", "我不认同", "为什么是", "凭什么")),
        ("request-example", ("举个例", "例子", "类似题", "示范一下")),
        ("check-step", ("这一步", "这样算", "这样写", "对不对", "帮我检查", "检查一下")),
        ("request-explanation", ("为什么", "讲解", "解释", "原理", "怎么理解")),
        ("express-confusion", ("不知道", "不会", "没思路", "看不懂", "不明白", "卡住", "懵")),
        ("off-topic", ("今天天气", "讲个笑话", "玩游戏", "你是谁", "吃什么")),
    )
    matched = next(
        ((intent, marker) for intent, markers in patterns for marker in markers if marker in text),
        None,
    )

    # “提交回答”意味着学生希望被判题；但明确的质疑或步骤检查比空泛文本
    # 更具体。它们不会覆盖结构化选项/画线等客观作答。
    if mode == "answer":
        if matched and matched[0] in {"challenge-answer", "check-step"}:
            return {
                "id": matched[0],
                "confidence": 0.94,
                "evidence": ["mode:answer", f"phrase:{matched[1]}"],
            }
        evidence.append("mode:answer")
        if text:
            evidence.append("student-text")
        return {"id": "submit-answer", "confidence": 0.92, "evidence": evidence}

    if matched:
        return {
            "id": matched[0],
            "confidence": 0.91,
            "evidence": ["mode:help", f"phrase:{matched[1]}"],
        }
    return {
        "id": "request-hint",
        "confidence": 0.82 if not text else 0.76,
        "evidence": ["mode:help", "empty-input" if not text else "student-text"],
    }


def _evidence_matches_student_input(evidence: str, student_input: str) -> bool:
    """检查诊断证据是否真的来自本轮输入，而不是模型自行补写理由。"""
    def normalize(text: str) -> str:
        return "".join(
            char.lower() for char in text if char.isalnum() or "\u4e00" <= char <= "\u9fff"
        )

    shown, submitted = normalize(evidence), normalize(student_input)
    # “对”“不会”等极短输入不包含足以支撑具体误区判断的上下文。即使模型
    # 原样引用，也只能作为待确认假设，不能显示成已诊断事实。
    if not shown or len(submitted) < 4:
        return False
    if shown in submitted or submitted in shown:
        return True
    match = SequenceMatcher(None, shown, submitted, autojunk=False).find_longest_match()
    return match.size >= 4 and match.size / min(len(shown), len(submitted)) >= 0.35


def normalize_misconception(
    value: Any,
    *,
    student_input: str | None = None,
) -> dict[str, Any]:
    """清洗模型提出的误区假设，并标记不可直接使用的诊断。

    误区是模型假设而非事实。没有具体学生证据，或置信度低于门槛时，必须
    进入确认状态；调用者只能用它追问，不能据此判题或推进学习阶段。
    """
    source = value if isinstance(value, dict) else {}
    raw_hypothesis = source.get("hypothesis")
    raw_evidence = source.get("evidence")
    hypothesis = _compact_text(raw_hypothesis, 160) if isinstance(raw_hypothesis, str) else ""
    evidence = _compact_text(raw_evidence, 240) if isinstance(raw_evidence, str) else ""
    raw_confidence = source.get("confidence", 0)
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = round(max(0.0, min(confidence, 1.0)), 3)
    # 只有模型调用边界提供了本轮原文时才重新验证证据来源。领域层再次清洗
    # 已规范化结果时保留 evidenceMatched，避免把“有文本”等同于“有证据”。
    evidence_matched = (
        _evidence_matches_student_input(evidence, student_input)
        if student_input is not None
        else bool(source.get("evidenceMatched", bool(evidence)))
    )
    needs_confirmation = (
        not hypothesis
        or not evidence
        or not evidence_matched
        or confidence < MISCONCEPTION_CONFIDENCE_THRESHOLD
        or bool(source.get("needsConfirmation"))
    )
    return {
        "hypothesis": hypothesis,
        "evidence": evidence,
        "evidenceMatched": evidence_matched,
        "confidence": confidence,
        "needsConfirmation": needs_confirmation,
    }


def select_teaching_action(
    *,
    intent: str,
    error_reason: str | None,
    current_stage: str,
    assessment: str,
    misconception: dict[str, Any] | None = None,
) -> str:
    """根据领域事实选择唯一教学动作，模型无权覆盖结果。"""
    diagnosis = normalize_misconception(misconception)
    if intent == "challenge-answer":
        return "run-self-check"
    if intent == "request-example":
        return "show-micro-example"
    if intent == "check-step":
        return "inspect-first-error"
    if diagnosis["needsConfirmation"] and diagnosis["hypothesis"]:
        return "ask-justification"
    if intent == "request-explanation":
        return "contrast-concepts" if error_reason == "concept" else "complete-step"
    if intent == "express-confusion":
        return "show-micro-example" if error_reason == "unknown" else "extract-conditions"
    if intent == "off-topic":
        return "extract-conditions"
    if current_stage == "verify":
        return "run-self-check"
    if current_stage == "practice":
        return "generate-micro-practice" if assessment == "correct" else "complete-step"
    if intent == "submit-answer":
        return {
            "correct": "ask-justification",
            "incorrect": "inspect-first-error",
            "partial": "complete-step",
        }.get(assessment, "inspect-first-error")
    return {
        "reading": "extract-conditions",
        "calculation": "inspect-first-error",
        "missing_step": "complete-step",
        "concept": "contrast-concepts",
        "unknown": "show-micro-example",
        "careless": "run-self-check",
    }.get(error_reason or "unknown", "extract-conditions")


def teaching_strategy_context(
    error_reason: str | None,
    current_stage: str,
    *,
    intent: dict[str, Any] | None = None,
    teaching_action: str | None = None,
) -> str:
    """生成可安全放入提示词的锁定策略与动作约束。"""
    reason = error_reason if error_reason in ERROR_STRATEGIES else "unknown"
    strategy, objective = ERROR_STRATEGIES[reason]
    intent_id = (intent or {}).get("id", "request-hint")
    action = teaching_action if teaching_action in TEACHING_ACTIONS else "extract-conditions"
    return (
        f"本轮教学策略：{strategy}（{objective}）。当前阶段：{current_stage}。"
        f"学生意图：{intent_id}。首选且唯一教学动作：{action}。"
        "只围绕这个动作推进一步；若误区证据不足或置信度低，唯一允许的替代动作是 ask-justification，"
        "此时必须追问确认而不是继续讲解。"
        "模型不得修改意图、教学动作、判题或阶段，也不要重复上一条提示。"
    )


def suggested_stage(
    current_stage: str,
    assessment: str,
    mode: str,
    *,
    assessment_authority: str = "guided",
    student_intent: str = "request-hint",
) -> str:
    """返回本轮唯一允许的阶段迁移结果。"""
    # 质疑标准答案时必须先复核。即使模型说“正确”，也不能把质疑当成一次
    # 掌握证据。普通模型的 correct 同样没有状态推进权限。
    if student_intent == "challenge-answer":
        return current_stage
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
    student_intent: dict[str, Any] | None = None,
    misconception: dict[str, Any] | None = None,
    generation_teaching_action: str | None = None,
) -> dict[str, Any]:
    """用已确认事实和确定性结果构建稳定、可序列化的教学计划。"""
    reason = error_reason if error_reason in ERROR_STRATEGIES else "unknown"
    strategy, objective = ERROR_STRATEGIES[reason]
    authority = "deterministic" if assessment_authority == "deterministic" else "guided"
    intent = student_intent or {
        "id": "submit-answer" if mode == "answer" else "request-hint",
        "confidence": 1.0,
        "evidence": [f"mode:{mode}"],
    }
    intent_id = intent.get("id") if intent.get("id") in STUDENT_INTENTS else "request-hint"
    try:
        intent_confidence = float(intent.get("confidence", 0))
    except (TypeError, ValueError):
        intent_confidence = 0.0
    intent_evidence = intent.get("evidence")
    if not isinstance(intent_evidence, list):
        intent_evidence = []
    safe_intent = {
        "id": intent_id,
        "confidence": max(0.0, min(intent_confidence, 1.0)),
        "evidence": [item[:80] for item in intent_evidence if isinstance(item, str) and item.strip()][:6],
    }
    diagnosis = normalize_misconception(misconception)
    next_stage = suggested_stage(
        current_stage,
        assessment,
        mode,
        assessment_authority=authority,
        student_intent=intent_id,
    )
    action_from_result = select_teaching_action(
        intent=intent_id,
        error_reason=reason,
        current_stage=current_stage,
        assessment=assessment,
        misconception=diagnosis,
    )
    generation_action = (
        generation_teaching_action
        if generation_teaching_action in TEACHING_ACTIONS
        else action_from_result
    )
    # 普通模型开始生成前已经锁定唯一动作，生成后的自报 assessment 无权
    # 偷换动作。唯一例外是模型给出了具体但证据不足的误区假设，此时降级为
    # 追问确认。确定性判题没有生成表达约束，可按客观结果选择动作。
    if authority == "guided" and generation_teaching_action in TEACHING_ACTIONS:
        action = (
            "ask-justification"
            if diagnosis["hypothesis"] and diagnosis["needsConfirmation"]
            else generation_action
        )
    else:
        action = action_from_result
    return {
        "intent": safe_intent,
        "assessment": assessment,
        "misconception": diagnosis,
        "errorStrategy": {"id": strategy, "objective": objective, "reason": reason},
        "teachingAction": action,
        # 仅确定性判题路径可显示标准答案；普通提示不能借计划越权泄题。
        "shouldRevealAnswer": mode == "answer" and authority == "deterministic" and assessment == "incorrect",
        "suggestedStage": next_stage,
        "replySource": reply_source,
        "audit": {
            "assessmentAuthority": authority,
            "stageAuthority": "tutor-turn-plan",
            "misconceptionConfirmed": not diagnosis["needsConfirmation"],
            "generationTeachingAction": generation_action,
            # 同一次生成允许的唯一调整是：模型发现证据不足后改为追问确认。
            # 记录这个差异，避免数据库声称模型执行了一个未进入提示词的动作。
            "teachingActionAdjusted": generation_action != action,
            "modelMayOverride": False,
        },
    }
