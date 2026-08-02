"""Deterministic checks for structured question answers.

Model feedback remains the fallback for open-ended reasoning. These checks are
deliberately small and explainable so common answer formats do not depend on a
second model call.
"""

from __future__ import annotations

import re
from fractions import Fraction
from typing import Any


def normalize_label(value: Any) -> str:
    return re.sub(r"[\s（）()]", "", str(value or "")).upper()


def normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", "", text)
    return text.replace("，", ",").replace("。", "").replace("；", ";")


def parse_number(value: Any) -> float | None:
    """Parse common textbook numeric answers, including simple fractions."""
    text = str(value or "").strip()
    text = text.replace(",", "").replace("，", "")
    text = re.sub(r"\\frac\s*\{\s*([^{}]+)\}\s*\{\s*([^{}]+)\}", r"(\1)/(\2)", text)
    text = re.sub(r"^\(\s*([-+]?\d+)\s*\)\s*/\s*\(\s*([-+]?\d+)\s*\)$", r"\1/\2", text)
    text = re.sub(r"[a-zA-Z°％%]+$", "", text).strip()
    if not text:
        return None
    try:
        if re.fullmatch(r"[-+]?\d+\s*/\s*[-+]?\d+", text):
            return float(Fraction(text.replace(" ", "")))
        return float(text)
    except (ValueError, ZeroDivisionError):
        return None


def number_matches(actual: Any, expected: Any, tolerance: Any = 0) -> bool:
    actual_number = parse_number(actual)
    expected_number = parse_number(expected)
    if actual_number is None or expected_number is None:
        return False
    try:
        allowed = max(0.0, float(tolerance or 0))
    except (TypeError, ValueError):
        allowed = 0.0
    return abs(actual_number - expected_number) <= allowed


def _check_single_answer(actual: Any, expected: list[Any], answer_type: str, tolerance: Any) -> bool:
    if answer_type == "numeric":
        return any(number_matches(actual, item, tolerance) for item in expected)
    normalized = normalize_text(actual)
    return bool(normalized) and any(normalized == normalize_text(item) for item in expected)


def _result(assessment: str, reply: str, hint: str) -> dict[str, Any]:
    return {
        "assessment": assessment,
        "reply": reply,
        "hint": hint,
        "knowledge": ["答案核对"],
        "stuckAt": "需要根据题目要求检查作答格式和关键结果。",
        "question": "你能指出答案中的哪个量或步骤支持这个结果吗？",
    }


def evaluate_structured_answer(
    question: dict[str, Any],
    student_input: str,
    interaction_result: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return a deterministic feedback payload when the question has a spec."""
    interaction = interaction_result or {}
    question_type = question.get("questionType")

    if question_type in {"choice", "multi-select"}:
        expected_values = question.get("correctAnswers") or []
        if not expected_values and question.get("correctAnswer"):
            expected_values = re.findall(r"\(?[A-H]\)?", str(question["correctAnswer"]))
        expected = {normalize_label(item) for item in expected_values if normalize_label(item)}
        submitted_values = interaction.get("selectedOptions")
        if not isinstance(submitted_values, list):
            submitted_values = re.findall(r"\(?[A-H]\)?", student_input)
        submitted = {normalize_label(item) for item in submitted_values if normalize_label(item)}
        if not expected or not submitted:
            return None
        if submitted == expected:
            return _result("correct", "选择正确。请再说明题干中的哪个条件支持这个选项。", "回到题干，找出支持该选项的关键条件。")
        return _result("incorrect", "这组选项还不正确。请重新检查每个选项，再提交一次。", "逐项核对选项与题干条件，不要只凭直觉选择。")

    if question_type == "fill-blank":
        blanks = question.get("blanks")
        answers = interaction.get("blankAnswers")
        if not isinstance(blanks, list) or not blanks or not isinstance(answers, dict):
            return None
        results: list[bool] = []
        for blank in blanks:
            if not isinstance(blank, dict):
                continue
            expected = blank.get("correctAnswers") or []
            if not isinstance(expected, list) or not expected:
                return None
            actual = answers.get(str(blank.get("id", "")), "")
            results.append(_check_single_answer(
                actual,
                expected,
                str(blank.get("answerType", "text")),
                blank.get("tolerance", 0),
            ))
        if not results:
            return None
        if all(results):
            return _result("correct", "填空全部正确。请说说这些结果是怎样从题目条件得到的。", "检查每个空之间的关系，并补全推导过程。")
        return _result("incorrect", "有些空还需要修改。请重新检查对应的运算或概念。", "先定位第一个不确定的空，再回到上一步条件。")

    if question_type == "numeric":
        spec = question.get("answerSpec")
        if not isinstance(spec, dict) or not spec.get("expected"):
            return None
        actual = interaction.get("numericAnswer") or student_input
        answer_type = str(spec.get("answerType", "numeric"))
        expected = spec.get("accepted") or [spec.get("expected")]
        if not isinstance(expected, list):
            expected = [expected]
        correct = _check_single_answer(actual, expected, answer_type, spec.get("tolerance", 0))
        if correct:
            return _result("correct", "答案正确。请再说明关键计算或公式依据。", "回看最后一步，确认结果和单位都符合题意。")
        return _result("incorrect", "这个结果还不正确。请检查运算、符号和单位后再试一次。", "从已知条件开始，逐步检查每一行计算。")

    return None
