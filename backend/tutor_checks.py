"""Deterministic tutor fallbacks and algebra step checks."""

from __future__ import annotations

import re
from typing import Any

from question_contracts import GUIDE_CARDS, HelpRequest, TutorReply


EQUATION_PATTERN = re.compile(
    r"(?<![0-9A-Za-z])([0-9A-Za-z]+(?:\s*[+\-*/]\s*[0-9A-Za-z]+)*\s*=\s*"
    r"-?\s*[0-9A-Za-z]+(?:\s*[+\-*/]\s*[0-9A-Za-z]+)*)(?![0-9A-Za-z])"
)


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
) -> TutorReply:
    normalized = request.studentInput.strip().lower()
    cards = guide_cards or GUIDE_CARDS
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
            canvasAction="show-triangles",
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
        canvasAction=card["canvasAction"],
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
