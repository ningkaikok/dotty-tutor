"""学生可见题目投影：面向学生的端点必须经过这一层。

为什么是白名单而不是黑名单：题目契约会持续增长（``subQuestions``、
``answerSpec``、``interaction`` 都是后加的），黑名单式脱敏每加一个带答案的
字段就会重新泄漏一次，而且不会有任何报错。白名单反过来——新字段默认不下发，
需要时显式加进来，代价是一次可见的缺字段，而不是一次静默的答案泄漏。

保留集是按两侧核对出来的：后端 ``lesson_generation`` 实际写入的键，以及前端
``QuestionAnswer`` / ``DrawLineCanvas`` / 错题 UI 实际读取的键。特别注意几个
容易误伤的：``answerSpec.unit`` 和 ``blanks[].unit`` 要显示单位；
``subQuestions[].evaluation`` 决定是否显示"此小问由陪练反馈"；
``interaction.points`` 是画线题的端点。而 ``interaction.requiredConnections``
是画线题的答案本身，前端从不读取。
"""

from __future__ import annotations

from typing import Any

# 题干、题型、渲染所需的结构；不含任何答案。
_QUESTION_KEEP = frozenset({
    "id",
    "label",
    "questionType",
    "selectionMode",
    "chapter",
    "knowledgePoint",
    "knowledgePointId",
    "questionNumber",
    "prompt",
    "givens",
    "options",
    "contentBlocks",
    "imageUrls",
    "imageReferences",
    "imageManifest",
    # 变式题的教学元数据与溯源信息，均由 variation_service 写入，不含答案。
    "variationOf",
    "variationStrategy",
    "variationStrategyVersion",
    "variationTarget",
    "variationObjective",
    "variationAttributionSource",
    "variationLevel",
})

# 需要递归脱敏的结构化字段。
_BLANK_KEEP = frozenset({"id", "label", "answerType", "unit"})
_ANSWER_SPEC_KEEP = frozenset({"answerType", "unit"})
_INTERACTION_KEEP = frozenset({"type", "instruction", "points"})
_SUB_QUESTION_KEEP = frozenset({
    "id", "label", "prompt", "questionType", "evaluation", "options", "contentBlocks",
})


def _pick(source: Any, keep: frozenset[str]) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    return {key: value for key, value in source.items() if key in keep}


def _student_blanks(blanks: Any) -> list[dict[str, Any]]:
    if not isinstance(blanks, list):
        return []
    return [_pick(blank, _BLANK_KEEP) for blank in blanks if isinstance(blank, dict)]


def _student_answer_spec(spec: Any) -> dict[str, Any] | None:
    """保留答案类型与单位（渲染输入框要用），剥掉 expected/accepted/tolerance。"""
    if not isinstance(spec, dict):
        return None
    return _pick(spec, _ANSWER_SPEC_KEEP)


def _student_interaction(interaction: Any) -> dict[str, Any] | None:
    """保留端点与说明，剥掉 requiredConnections——那是画线题的标准答案。"""
    if not isinstance(interaction, dict):
        return None
    return _pick(interaction, _INTERACTION_KEEP)


def _student_sub_question(part: Any) -> dict[str, Any]:
    projected = _pick(part, _SUB_QUESTION_KEEP)
    if not isinstance(part, dict):
        return projected
    if "blanks" in part:
        projected["blanks"] = _student_blanks(part.get("blanks"))
    if "answerSpec" in part:
        projected["answerSpec"] = _student_answer_spec(part.get("answerSpec"))
    if "interaction" in part:
        projected["interaction"] = _student_interaction(part.get("interaction"))
    return projected


def student_question(question: Any) -> dict[str, Any]:
    """把一道题投影成学生可见形态；未列入保留集的字段一律不下发。"""
    if not isinstance(question, dict):
        return {}
    projected = _pick(question, _QUESTION_KEEP)
    if "blanks" in question:
        projected["blanks"] = _student_blanks(question.get("blanks"))
    if "answerSpec" in question:
        projected["answerSpec"] = _student_answer_spec(question.get("answerSpec"))
    if "interaction" in question:
        projected["interaction"] = _student_interaction(question.get("interaction"))
    sub_questions = question.get("subQuestions")
    if isinstance(sub_questions, list):
        projected["subQuestions"] = [
            _student_sub_question(part) for part in sub_questions if isinstance(part, dict)
        ]
    return projected


def student_question_payload(payload: Any) -> dict[str, Any]:
    """投影整个 questionPayload，只替换 question，其余键交给调用方决定。"""
    if not isinstance(payload, dict):
        return {}
    projected = dict(payload)
    projected["question"] = student_question(payload.get("question"))
    return projected
