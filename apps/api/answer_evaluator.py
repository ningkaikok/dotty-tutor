"""结构化题型的确定性判题器。

为什么刻意不引入第二次模型调用来判题：

1. 可解释——每个判定都能追溯到"归一化后是否相等/容差内"，出错可以定位到具体规则；
2. 可回归——规则是纯函数，坏样本可以直接变成单测，模型行为漂移不会污染学习记录；
3. 成本与延迟——判题发生在学生每次提交时，走模型会把交互拖慢数秒。

边界约束：本模块只回答"对/不对"，绝不输出"错在哪类"的诊断。仅凭最终答案无法区分
符号错误、概念错误还是计算错误，误区诊断由 Tutor Turn Plan 基于证据假设完成。
没有 answerSpec/blanks 的开放题返回 None，交回给模型和分层引导卡兜底。

归一化函数处理教材常见的答案格式：全角括号/标点、`\\frac{a}{b}` LaTeX 分数、
`(a)/(b)` 括号分数、百分号和单位后缀（°、% 等）、千分位逗号。
"""

from __future__ import annotations

import re
from fractions import Fraction
from typing import Any


def normalize_label(value: Any) -> str:
    """选项标签归一化：去掉空白和全角/半角括号并大写，使 (B)、b、B 等价。"""
    return re.sub(r"[\s（）()]", "", str(value or "")).upper()


def normalize_text(value: Any) -> str:
    """填空文本归一化：忽略空白大小写差异，统一教材常见的全角标点。"""
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", "", text)
    return text.replace("，", ",").replace("。", "").replace("；", ";")


def parse_number(value: Any) -> float | None:
    """解析教材常见数字答案，包括简单分数；解析失败返回 None 而不是抛错。

    依次处理：千分位逗号 → `\\frac{a}{b}` LaTeX 分数 → `(a)/(b)` 括号分数
    → 单位/百分号后缀；`a/b` 形式用精确分数运算避免浮点误差。
    """
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


# 判据器版本参与证据溯源：evidence 的字段语义变化时必须递增，
# 消费方（陪练计划、尝试记录）据此解释历史证据的结构。
EVALUATOR_VERSION = "answer-evaluator-v1"


def _has_submitted_sub_answer(value: Any) -> bool:
    """Return whether a tutor-only sub-question has meaningful student input."""
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(_has_submitted_sub_answer(item) for item in value)
    if isinstance(value, dict):
        return any(_has_submitted_sub_answer(item) for item in value.values())
    return value is not None


# 判断题的否定式必须先于肯定式匹配："不正确"里含有"正确"。
# 旧的内联实现按肯定式优先，因此把"不正确"判成了"正确"——在只影响陪练文案时
# 这只是措辞不准，一旦判定成为掌握度的权威来源就会写错学习记录。
_TRUE_FALSE_NEGATIVE = ("不正确", "不对", "错误", "false")
_TRUE_FALSE_POSITIVE = ("正确", "对", "true")


def normalize_true_false(value: Any) -> str | None:
    """把判断题的作答或标准答案归一化为 ``正确`` / ``错误``；无法识别返回 None。

    标准答案与学生作答走同一个函数，避免出现"correctAnswer 写成 True、
    学生答"正确"却判错"这类两侧口径不一致的问题。
    """
    text = str(value or "").strip().lower()
    if not text:
        return None
    if any(marker in text for marker in _TRUE_FALSE_NEGATIVE):
        return "错误"
    if any(marker in text for marker in _TRUE_FALSE_POSITIVE):
        return "正确"
    return None


def _result(
    assessment: str,
    reply: str,
    hint: str,
    evidence: dict[str, Any] | None = None,
    *,
    knowledge: list[str] | None = None,
    stuck_at: str | None = None,
    follow_up: str | None = None,
) -> dict[str, Any]:
    """构造判定结果。

    ``knowledge``/``stuck_at``/``follow_up`` 允许按题型给出更贴题的引导措辞；
    不传时沿用通用文案。这几个字段会流进引导卡（见 ``domain/tutoring/checks.py``），
    所以能贴题就不要退化成通用句。
    """
    payload = {
        "assessment": assessment,
        "reply": reply,
        "hint": hint,
        "knowledge": knowledge or ["答案核对"],
        "stuckAt": stuck_at or "需要根据题目要求检查作答格式和关键结果。",
        "question": follow_up or "你能指出答案中的哪个量或步骤支持这个结果吗？",
    }
    # 客观判定证据：只包含学生侧已知的事实（自己的作答、哪些空未匹配、容差等）。
    # 绝不放入标准答案/期望标签——该结构会进入学生可见的 guideContext。
    if evidence is not None:
        evidence["evaluatorVersion"] = EVALUATOR_VERSION
        payload["evaluationEvidence"] = evidence
    return payload


def evaluate_structured_answer(
    question: dict[str, Any],
    student_input: str,
    interaction_result: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return a deterministic feedback payload when the question has a spec.

    按题型分发的确定性判定入口。返回 None 表示该题没有可确定性判定的答案规格，
    调用方必须回退到模型反馈，而不是把 None 当作"答错"。
    """
    interaction = interaction_result or {}
    sub_questions = question.get("subQuestions")
    if isinstance(sub_questions, list) and sub_questions:
        return _evaluate_sub_questions(sub_questions, student_input, interaction)
    question_type = question.get("questionType")

    if question_type == "true-false":
        expected = normalize_true_false(question.get("correctAnswer"))
        selected = normalize_true_false(student_input)
        if not expected or not selected:
            return None
        evidence = {"strategy": "true-false-match", "submittedLabel": selected}
        # 判断题只有两个选项，"这个判断不对"本身就等于说出了答案，因此这里
        # 不再像旧实现那样显式复述 correctAnswer——与其余题型的措辞保持一致。
        knowledge = [str(question.get("knowledgePoint") or "概念判断")]
        stuck_at = "需要根据题干条件判断命题真伪。"
        follow_up = "题干中的哪条条件能支持你的判断？"
        if selected == expected:
            return _result(
                "correct", "判断正确。请再说说题干中的哪个条件支持这个判断。",
                "圈出题干中的关键条件，再逐项核对命题。", evidence,
                knowledge=knowledge, stuck_at=stuck_at, follow_up=follow_up,
            )
        return _result(
            "incorrect", "这个判断不对。请回到题干，找出能验证这句话的条件。",
            "圈出题干中的关键条件，再逐项核对命题。", evidence,
            knowledge=knowledge, stuck_at=stuck_at, follow_up=follow_up,
        )

    if question_type in {"choice", "multi-select"}:
        # 多选按完整选项集合比较：多选、少选、错选都判 incorrect，不做部分给分。
        expected_values = question.get("correctAnswers") or []
        # 兼容旧数据：correctAnswer 是 "AB"/"(A)(C)" 之类的字符串时提取选项标签。
        if not expected_values and question.get("correctAnswer"):
            expected_values = re.findall(r"\(?[A-H]\)?", str(question["correctAnswer"]))
        expected = {normalize_label(item) for item in expected_values if normalize_label(item)}
        submitted_values = interaction.get("selectedOptions")
        if not isinstance(submitted_values, list):
            submitted_values = re.findall(r"\(?[A-H]\)?", student_input)
        submitted = {normalize_label(item) for item in submitted_values if normalize_label(item)}
        if not expected or not submitted:
            return None
        evidence = {
            "strategy": "choice-set-match",
            "submittedLabels": sorted(submitted),
            "expectedCount": len(expected),
        }
        if submitted == expected:
            return _result("correct", "选择正确。请再说明题干中的哪个条件支持这个选项。", "回到题干，找出支持该选项的关键条件。", evidence)
        return _result("incorrect", "这组选项还不正确。请重新检查每个选项，再提交一次。", "逐项核对选项与题干条件，不要只凭直觉选择。", evidence)

    if question_type == "fill-blank":
        blanks = question.get("blanks")
        answers = interaction.get("blankAnswers")
        if not isinstance(blanks, list) or not blanks or not isinstance(answers, dict):
            return None
        results: list[bool] = []
        failed_blank_ids: list[str] = []
        for blank in blanks:
            if not isinstance(blank, dict):
                continue
            expected = blank.get("correctAnswers") or []
            if not isinstance(expected, list) or not expected:
                return None
            actual = answers.get(str(blank.get("id", "")), "")
            matched = _check_single_answer(
                actual,
                expected,
                str(blank.get("answerType", "text")),
                blank.get("tolerance", 0),
            )
            results.append(matched)
            if not matched:
                failed_blank_ids.append(str(blank.get("id", "")))
        if not results:
            return None
        evidence = {
            "strategy": "fill-blank-parts",
            "totalBlanks": len(results),
            "matchedCount": sum(1 for item in results if item),
            "failedBlankIds": failed_blank_ids,
        }
        if all(results):
            return _result("correct", "填空全部正确。请说说这些结果是怎样从题目条件得到的。", "检查每个空之间的关系，并补全推导过程。", evidence)
        return _result("incorrect", "有些空还需要修改。请重新检查对应的运算或概念。", "先定位第一个不确定的空，再回到上一步条件。", evidence)

    if question_type == "numeric":
        spec = question.get("answerSpec")
        if not isinstance(spec, dict) or not spec.get("expected"):
            return None
        actual = interaction.get("numericAnswer") or student_input
        answer_type = str(spec.get("answerType", "numeric"))
        expected = spec.get("accepted") or [spec.get("expected")]
        if not isinstance(expected, list):
            expected = [expected]
        try:
            tolerance_value = max(0.0, float(spec.get("tolerance", 0) or 0))
        except (TypeError, ValueError):
            tolerance_value = 0.0
        evidence = {
            "strategy": "numeric-tolerance",
            "submittedRaw": str(actual or "")[:40],
            "tolerance": tolerance_value,
            "expectedCount": len(expected),
        }
        correct = _check_single_answer(actual, expected, answer_type, spec.get("tolerance", 0))
        if correct:
            return _result("correct", "答案正确。请再说明关键计算或公式依据。", "回看最后一步，确认结果和单位都符合题意。", evidence)
        return _result("incorrect", "这个结果还不正确。请检查运算、符号和单位后再试一次。", "从已知条件开始，逐步检查每一行计算。", evidence)

    if question_type == "short-answer" and (
        isinstance(question.get("evaluation"), dict)
        and question["evaluation"].get("mode") == "deterministic"
    ):
        expected_values = question.get("correctAnswers") or []
        if not expected_values and question.get("correctAnswer") not in (None, ""):
            expected_values = [question["correctAnswer"]]
        actual = interaction.get("text") or student_input
        if not expected_values or not str(actual).strip():
            return None
        correct = _check_single_answer(actual, expected_values, "text", 0)
        evidence = {
            "strategy": "short-answer-text-match",
            "submittedRaw": str(actual)[:80],
            "expectedCount": len(expected_values),
        }
        return _result(
            "correct" if correct else "incorrect",
            "作答正确。请再说明依据。" if correct else "文字答案还不正确，请回到题干检查关键概念。",
            "先圈出题干中的关键词，再逐句核对你的答案。",
            evidence,
        )

    if question_type == "draw-line":
        interaction_spec = question.get("interaction")
        required = interaction_spec.get("requiredConnections") if isinstance(interaction_spec, dict) else None
        submitted = interaction.get("connections")
        if not isinstance(required, list) or not required or not isinstance(submitted, list) or not submitted:
            return None

        def normalize_connection(item: Any) -> tuple[str, str] | None:
            if not isinstance(item, list) or len(item) != 2:
                return None
            values = sorted(str(value) for value in item)
            return values[0], values[1]

        expected_connections = {item for item in (normalize_connection(value) for value in required) if item}
        submitted_connections = {item for item in (normalize_connection(value) for value in submitted) if item}
        correct = submitted_connections == expected_connections
        assessment = "correct" if correct else "partial" if submitted_connections & expected_connections else "incorrect"
        evidence = {
            "strategy": "line-connections",
            "submittedCount": len(submitted_connections),
            "requiredCount": len(expected_connections),
        }
        return _result(
            assessment,
            "连接关系正确。请说说这些点之间为什么应当这样对应。" if correct else "已完成部分连接，请逐条核对剩余对应点。" if assessment == "partial" else "连接关系还不正确，请逐条核对对应点。",
            "按题目给出的关系逐条检查起点和终点。",
            evidence,
        )

    return None


def _evaluate_sub_questions(
    sub_questions: list[Any],
    student_input: str,
    interaction: dict[str, Any],
) -> dict[str, Any] | None:
    """Evaluate each structured sub-question without giving tutor-only parts a score."""
    answers = interaction.get("subQuestionAnswers")
    if not isinstance(answers, dict):
        answers = {}
    parts: list[dict[str, Any]] = []
    deterministic_count = 0
    matched_count = 0
    ungraded_count = 0
    has_tutor_part = False
    has_incomplete_part = False
    has_incorrect_part = False

    for index, raw_sub_question in enumerate(sub_questions, start=1):
        if not isinstance(raw_sub_question, dict):
            continue
        sub_id = str(raw_sub_question.get("id") or f"sq-{index}")
        evaluation = raw_sub_question.get("evaluation")
        mode = evaluation.get("mode") if isinstance(evaluation, dict) else None
        answer = answers.get(sub_id)
        answer = answer if isinstance(answer, dict) else {}
        if not answer and len(sub_questions) == 1 and student_input.strip():
            answer = {"text": student_input}
        if mode == "tutor":
            has_tutor_part = True
            submitted = _has_submitted_sub_answer(answer.get("text")) or _has_submitted_sub_answer(answer)
            parts.append({
                "subQuestionId": sub_id,
                "status": "tutor" if submitted else "incomplete",
                "feedbackRequired": True,
            })
            if not submitted:
                has_incomplete_part = True
            ungraded_count += 1
            continue

        deterministic_count += 1
        result = evaluate_structured_answer(raw_sub_question, "", answer)
        if result is None:
            parts.append({"subQuestionId": sub_id, "status": "ungraded", "feedbackRequired": True})
            ungraded_count += 1
            has_incomplete_part = True
            continue
        matched = result.get("assessment") == "correct"
        parts.append({"subQuestionId": sub_id, "status": "correct" if matched else "incorrect"})
        if matched:
            matched_count += 1
        else:
            has_incorrect_part = True

    if not parts:
        return None
    complete = not has_incomplete_part
    # Eligibility describes whether this question can produce objective
    # evidence, not whether this particular submission was correct. A wrong
    # deterministic answer is still valuable negative evidence for mastery;
    # only incomplete/ungraded parts and tutor-only parts exclude the attempt.
    mastery_eligible = complete and not has_tutor_part and ungraded_count == 0
    if has_incorrect_part:
        assessment = "incorrect"
        reply = "有些小问还不正确。请按小问顺序检查每一步，再重新提交。"
        hint = "先定位第一个标记为需要修正的小问，回到它的已知条件。"
    elif not complete:
        assessment = "partial"
        reply = "还需要完成所有小问，再一起检查答案。"
        hint = "按小问编号逐项作答；开放性小问请写出你的理由。"
    elif has_tutor_part:
        assessment = "partial"
        reply = "可判分的小问已完成；陪练小问会由老师继续反馈，暂不记为客观全对。"
        hint = "继续补充陪练小问的理由或推导过程。"
    else:
        assessment = "correct"
        reply = "各小问答案正确。请再回顾每一步为什么成立。"
        hint = "检查每个小问使用的条件，并说出关键依据。"
    evidence = {
        "strategy": "sub-question-parts",
        "parts": parts,
        "gradableCount": deterministic_count,
        "matchedCount": matched_count,
        "ungradedCount": ungraded_count,
        "complete": complete,
        "masteryEligible": mastery_eligible,
    }
    result = _result(assessment, reply, hint, evidence)
    result["evaluationSummary"] = {
        "strategy": "sub-question-parts",
        "parts": parts,
        "gradableCount": deterministic_count,
        "matchedCount": matched_count,
        "ungradedCount": ungraded_count,
        "complete": complete,
        "masteryEligible": mastery_eligible,
    }
    return result
