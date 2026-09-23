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
_PAYLOAD_KEEP = frozenset({"question", "lessonSteps"})
_LESSON_STEP_KEEP = frozenset({"id", "title", "text", "speechText", "action"})
_VARIATION_KEEP = frozenset({
    "variationId", "mistakeId", "learnerId", "strategy", "attributionSource", "level",
    "sequence", "questionPayload", "status", "assessment", "response", "feedback",
    "createdAt", "answeredAt", "attemptId", "evaluationEvidence", "tutorStage", "mastery",
    "policy", "gate", "nextAction",
})
_REVIEW_KEEP = frozenset({
    "taskId", "mistakeId", "learnerId", "intervalDays", "dueAt", "status", "questionPayload",
    "response", "evaluationEvidence", "assessment", "feedback", "createdAt", "startedAt",
    "completedAt", "mistake", "scheduleVersion", "sequenceNo", "profile", "objectiveType",
    "gateMode", "policyVersion", "triggerEvidenceRef", "supersededAt", "policy", "gate", "nextAction",
})
_REVIEW_MISTAKE_KEEP = frozenset({"chapter", "knowledgePoint", "prompt"})
_THREAD_KEEP = frozenset({
    "threadId", "mistakeId", "learnerId", "stage", "summary", "hintLevel", "messageCount",
    "createdAt", "updatedAt", "messages",
})
_TUTOR_MESSAGE_KEEP = frozenset({
    "messageId", "threadId", "role", "content", "inputMode", "assessment", "action", "createdAt",
})
_TUTOR_REPLY_KEEP = frozenset({"reply", "guideContext", "nextHintLevel", "canvasAction", "source"})
_INTERNAL_TUTOR_KEYS = frozenset({
    "stageArtifacts", "solution", "modelRun", "quality", "review", "sourceProvenance",
    "verification", "cacheKey", "sourceImagePath", "correctAnswer", "correctAnswers",
    "expected", "accepted", "requiredConnections",
})
_MISTAKE_KEEP = frozenset({
    "mistakeId", "learnerId", "sourceFilename", "contentType", "sourceImageUrl",
    "questionPayload", "guideCards", "originalAnswer", "subject", "gradeBand",
    "chapter", "knowledgePoint", "errorReason", "aiErrorReason",
    "aiErrorReasonConfidence", "notes", "status", "createdAt", "updatedAt",
    "confirmedAt",
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
    """按顶层白名单投影 questionPayload，内部阶段和模型字段默认全部丢弃。"""
    if not isinstance(payload, dict):
        return {}
    projected = {key: value for key, value in payload.items() if key in _PAYLOAD_KEEP}
    projected["question"] = student_question(payload.get("question"))
    if isinstance(payload.get("lessonSteps"), list):
        projected["lessonSteps"] = [
            _pick(step, _LESSON_STEP_KEEP)
            for step in payload["lessonSteps"]
            if isinstance(step, dict)
        ]
    return projected


def student_mistake_item(item: Any) -> dict[str, Any]:
    """Project a mistake record while keeping the Store payload private.

    This is shared by the mistake CRUD routes and the learning endpoint's ``autoMistake``
    response so a student cannot receive the full server-side question snapshot through a
    second API path.
    """
    if not isinstance(item, dict):
        return {}
    projected = {key: value for key, value in item.items() if key in _MISTAKE_KEEP}
    projected["questionPayload"] = student_question_payload(item.get("questionPayload"))
    return projected


def student_variation_item(item: Any) -> dict[str, Any]:
    """Project a generated variation while keeping grading data server-side."""
    projected = _pick(item, _VARIATION_KEEP)
    projected["questionPayload"] = student_question_payload(
        item.get("questionPayload") if isinstance(item, dict) else None
    )
    if isinstance(item, dict) and isinstance(item.get("reviewTasks"), list):
        projected["reviewTasks"] = [student_review_task(task) for task in item["reviewTasks"]]
    return projected


def student_review_task(item: Any) -> dict[str, Any]:
    """Project a spaced-review task without exposing its model run or answer key."""
    projected = _pick(item, _REVIEW_KEEP)
    projected["questionPayload"] = student_question_payload(
        item.get("questionPayload") if isinstance(item, dict) else None
    )
    mistake = item.get("mistake") if isinstance(item, dict) else None
    if isinstance(mistake, dict):
        projected["mistake"] = _pick(mistake, _REVIEW_MISTAKE_KEEP)
    return projected


def student_tutor_message(item: Any) -> dict[str, Any]:
    """Project a persisted tutoring message without internal model metadata."""
    projected = _pick(item, _TUTOR_MESSAGE_KEEP)
    if isinstance(item, dict) and isinstance(item.get("action"), dict):
        projected["action"] = student_tutor_action(item["action"])
    return projected


def student_tutor_thread(item: Any) -> dict[str, Any]:
    """Project a tutoring thread and its messages for the student application."""
    projected = _pick(item, _THREAD_KEEP)
    if isinstance(item, dict) and isinstance(item.get("messages"), list):
        projected["messages"] = [student_tutor_message(message) for message in item["messages"]]
    return projected


def student_tutor_reply(item: Any) -> dict[str, Any]:
    """Project one tutor reply; model/provider details are server-only."""
    return _pick(item, _TUTOR_REPLY_KEEP)


def student_tutor_action(item: Any) -> dict[str, Any]:
    """Keep the explainable action while removing its internal model run."""
    projected = _pick(item, frozenset({
        "type", "previousStage", "nextStage", "assessment", "prompt", "tutorTurnPlan",
        "deduplication", "toolPolicy",
    }))
    for key in ("tutorTurnPlan", "deduplication"):
        if key in projected:
            projected[key] = _remove_internal_tutor_fields(projected[key])
    return projected


def _remove_internal_tutor_fields(value: Any) -> Any:
    """Recursively remove internal fields from legacy or future tutor actions."""
    if isinstance(value, dict):
        return {
            key: _remove_internal_tutor_fields(item)
            for key, item in value.items()
            if key not in _INTERNAL_TUTOR_KEYS
        }
    if isinstance(value, list):
        return [_remove_internal_tutor_fields(item) for item in value]
    return value
