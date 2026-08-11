"""Deterministic tutor fallbacks and algebra step checks."""

from __future__ import annotations

import re
from typing import Any

from question_contracts import CANVAS_ACTIONS, GUIDE_CARDS, HelpRequest, TutorReply


EQUATION_PATTERN = re.compile(
    r"(?<![0-9A-Za-z])([0-9A-Za-z]+(?:\s*[+\-*/]\s*[0-9A-Za-z]+)*\s*=\s*"
    r"-?\s*[0-9A-Za-z]+(?:\s*[+\-*/]\s*[0-9A-Za-z]+)*)(?![0-9A-Za-z])"
)

# 画布当前只实现了几何演示。旧数据或模型故障时如果把几何引导卡复用到
# 其他题目，就会出现“体重题显示 show-triangles”这类跨题污染；所有动作
# 进入 TutorEngine 前都要经过下面的题目上下文判断。
GEOMETRY_MARKERS = (
    "几何", "三角形", "垂直", "平分线", "轨迹", "圆", "角平分", "中点",
    "等腰", "全等", "parallel", "triangle", "bisector",
)


def is_geometry_question(question: dict[str, Any] | None) -> bool:
    """判断题目是否真的需要几何画布，而不是依据历史引导卡猜测。"""
    if not isinstance(question, dict):
        return False
    if question.get("questionType") == "draw-line":
        return True
    haystack = " ".join(
        str(question.get(key) or "")
        for key in ("chapter", "knowledgePoint", "prompt", "givens")
    ).lower()
    return any(marker.lower() in haystack for marker in GEOMETRY_MARKERS)


def generic_guide_cards(question: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """为非几何题提供不带领域假设的引导卡，避免回退到几何样例。"""
    knowledge = str((question or {}).get("knowledgePoint") or "本题知识点")
    return [
        {
            "level": 0,
            "stuckAt": "还没有把题目条件整理成可检查的第一步。",
            "knowledge": [knowledge],
            "hint": "先圈出题目给出的数值、单位或关键词，再说明题目要求什么。",
            "question": "根据题目条件，你准备先写出哪一步？",
            "canvasAction": "show-base",
        },
        {
            "level": 1,
            "stuckAt": "已经找到条件，但还没有把它们用于当前问题。",
            "knowledge": [knowledge],
            "hint": "把已知条件逐项代入对应的定义、公式或比较规则。",
            "question": "代入后你得到什么结果？请说出中间一步。",
            "canvasAction": "show-base",
        },
        {
            "level": 2,
            "stuckAt": "需要检查计算结果是否回答了题目本身。",
            "knowledge": [knowledge],
            "hint": "回看题目要求，并检查数值、单位和符号是否一致。",
            "question": "你的结果与题目要求的量相符吗？",
            "canvasAction": "show-base",
        },
    ]


def normalize_guide_cards(
    cards: list[dict[str, Any]] | None,
    question: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """清洗历史/模型引导卡，并禁止非几何题产生几何画布动作。"""
    geometry = is_geometry_question(question)
    raw_cards = cards if isinstance(cards, list) else []
    card_text = " ".join(
        str(card.get(key) or "")
        for card in raw_cards
        if isinstance(card, dict)
        for key in ("stuckAt", "knowledge", "hint", "question", "canvasAction")
    ).lower()
    # 这组词只用于识别旧的内置几何样例，不会替换正常的几何题卡片。
    stale_geometry = not geometry and any(marker in card_text for marker in ("三角形", "垂直平分线", "pam", "pbm", "show-triangles"))
    if not raw_cards or stale_geometry:
        return generic_guide_cards(question)

    normalized: list[dict[str, Any]] = []
    fallback_actions = ("show-triangles", "show-triangles", "show-bisector")
    for index in range(3):
        source = raw_cards[index] if index < len(raw_cards) and isinstance(raw_cards[index], dict) else {}
        fallback = generic_guide_cards(question)[index]
        action = source.get("canvasAction") if geometry else "show-base"
        if action not in CANVAS_ACTIONS:
            action = fallback_actions[index] if geometry else "show-base"
        normalized.append({
            "level": index,
            "stuckAt": str(source.get("stuckAt") or fallback["stuckAt"])[:300],
            "knowledge": source.get("knowledge") if isinstance(source.get("knowledge"), list) else fallback["knowledge"],
            "hint": str(source.get("hint") or fallback["hint"])[:500],
            "question": str(source.get("question") or fallback["question"])[:500],
            "canvasAction": action,
        })
    return normalized


def safe_canvas_action(question: dict[str, Any] | None, action: Any) -> str:
    """只让几何题使用三角形/垂直平分线等动作，其他题统一使用基础画布。"""
    if not is_geometry_question(question):
        return "show-base"
    return action if action in CANVAS_ACTIONS else "show-base"


def mock_model_run(requested_provider: str = "mock", error: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "requestedProvider": requested_provider,
        "provider": "mock",
        "model": "static-demo",
        "fallback": requested_provider != "mock",
    }
    if error:
        result["error"] = error
    return result


def build_reply(
    request: HelpRequest,
    guide_cards: list[dict[str, Any]] | None = None,
    model_run: dict[str, Any] | None = None,
    question: dict[str, Any] | None = None,
) -> TutorReply:
    normalized = request.studentInput.strip().lower()
    cards = normalize_guide_cards(guide_cards, question)
    run = model_run or mock_model_run()
    if any(marker in normalized for marker in ("垂直平分线", "perpendicular bisector")):
        guide_context = {
            "assessment": "correct",
            "stuckAt": "学生已经提出正确猜想，需要补全证明。",
            "knowledge": ["全等三角形", "垂直平分线"],
            "hint": "不要停在结论；用 PA = PB、AM = BM 和公共边 PM 说明理由。",
            "question": "你能用 SSS 全等把这个结论证明完整吗？",
        }
        return TutorReply(
            reply="这个猜想是对的。先别急着结束：请说明三角形 PAM 与 PBM 为什么全等。",
            guideContext=guide_context,
            nextHintLevel=min(request.hintLevel + 1, 3),
            canvasAction=safe_canvas_action(question, "show-triangles"),
            source="answer-check",
            modelRun=run,
        )

    card = cards[min(request.hintLevel, len(cards) - 1)]
    stuck_markers = ("不知道", "不会", "没思路", "卡住", "don't know", "stuck")
    has_attempt = bool(normalized) and not any(marker in normalized for marker in stuck_markers)
    prefix = (
        "我会先核对你写的这一步。"
        if request.mode == "answer" and has_attempt
        else "我看到你已经写了一些思路。" if has_attempt
        else "没关系，我们只往前走一步。"
    )
    return TutorReply(
        reply=f"{prefix}{card['hint']}\n\n{card['question']}",
        guideContext={
            "assessment": "partial" if has_attempt else "incorrect",
            "stuckAt": card["stuckAt"],
            "knowledge": card["knowledge"],
            "hint": card["hint"],
            "question": card["question"],
        },
        nextHintLevel=min(request.hintLevel + 1, 3),
        canvasAction=safe_canvas_action(question, card.get("canvasAction")),
        source="stored-guide-card",
        modelRun=run,
    )


def linear_equation_form(equation: str) -> tuple[float, float] | None:
    if equation.count("=") != 1:
        return None

    def expression_form(expression: str) -> tuple[float, float] | None:
        normalized = expression.replace(" ", "").replace("*", "")
        terms = re.findall(r"[+-]?[^+-]+", normalized)
        coefficient = 0.0
        constant = 0.0
        try:
            for term in terms:
                if term.endswith("x"):
                    shown = term[:-1]
                    coefficient += 1.0 if shown in ("", "+") else -1.0 if shown == "-" else float(shown)
                elif "x" in term or not term:
                    return None
                else:
                    constant += float(term)
        except ValueError:
            return None
        return coefficient, constant

    left, right = equation.split("=", 1)
    left_form = expression_form(left)
    right_form = expression_form(right)
    if not left_form or not right_form:
        return None
    return left_form[0] - right_form[0], left_form[1] - right_form[1]


def equivalent_linear_equations(first: str, second: str) -> bool | None:
    first_form = linear_equation_form(first)
    second_form = linear_equation_form(second)
    if not first_form or not second_form:
        return None
    first_a, first_b = first_form
    second_a, second_b = second_form
    if abs(first_a) < 1e-9 or abs(second_a) < 1e-9:
        return None
    return abs(first_a * second_b - second_a * first_b) < 1e-7


def equation_conflict(
    student_input: str,
    lesson_steps: list[dict[str, Any]],
    question_prompt: str = "",
) -> tuple[str, str] | None:
    student_equations = [re.sub(r"\s+", "", item) for item in EQUATION_PATTERN.findall(student_input)]
    question_equations = [re.sub(r"\s+", "", item) for item in EQUATION_PATTERN.findall(question_prompt)]
    for student_equation in student_equations:
        for question_equation in question_equations:
            if equivalent_linear_equations(student_equation, question_equation) is False:
                return student_equation, question_equation

    reference_text = "\n".join(
        f"{step.get('text', '')} {step.get('speechText', '')}" for step in lesson_steps
    )
    reference_equations = [re.sub(r"\s+", "", item) for item in EQUATION_PATTERN.findall(reference_text)]
    for student_equation in student_equations:
        student_left = student_equation.split("=", 1)[0]
        for reference_equation in reference_equations:
            if reference_equation.split("=", 1)[0] == student_left and reference_equation != student_equation:
                return student_equation, reference_equation
    return None
