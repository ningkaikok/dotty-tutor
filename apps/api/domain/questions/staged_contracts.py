"""原题抽取、独立求解和教学脚本的严格模型契约。"""

from __future__ import annotations

from typing import Any

from domain.questions.contracts import (
    QuestionType,
    _answer_spec_schema,
    _blank_item_schema,
)


def _string_list(max_items: int, max_length: int) -> dict[str, Any]:
    return {"type": "array", "maxItems": max_items, "items": {"type": "string", "maxLength": max_length}}


QUESTION_EXTRACTION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "schemaVersion": {"type": "string", "maxLength": 40},
        "questionNumber": {"type": "string", "maxLength": 20},
        "stem": {"type": "string", "maxLength": 4_000},
        "questionType": {"type": "string", "enum": list(QuestionType.__args__)},
        "chapter": {"type": "string", "maxLength": 80},
        "knowledgePoint": {"type": "string", "maxLength": 120},
        "givens": _string_list(8, 160),
        "options": _string_list(8, 240),
        "subQuestions": {"type": "array", "maxItems": 12, "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"id": {"type": "string", "maxLength": 30}, "label": {"type": "string", "maxLength": 20}, "prompt": {"type": "string", "maxLength": 800}},
            "required": ["id", "label", "prompt"],
        }},
        "imageReferences": _string_list(12, 160),
    },
    "required": ["schemaVersion", "questionNumber", "stem", "questionType", "chapter", "knowledgePoint", "givens", "options", "subQuestions", "imageReferences"],
}


SOLUTION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "schemaVersion": {"type": "string", "maxLength": 40},
        "questionType": {"type": "string", "enum": list(QuestionType.__args__)},
        "correctAnswer": {"type": "string", "maxLength": 160},
        "correctAnswers": _string_list(8, 160),
        "blanks": {"type": "array", "maxItems": 8, "items": _blank_item_schema()},
        "answerSpec": _answer_spec_schema(),
        "interaction": {"type": "object", "additionalProperties": False, "properties": {
            "type": {"type": "string", "enum": ["none", "draw-line"]},
            "instruction": {"type": "string", "maxLength": 160},
            "points": {"type": "array", "maxItems": 12, "items": {"type": "object", "additionalProperties": False, "properties": {"id": {"type": "string", "maxLength": 8}, "label": {"type": "string", "maxLength": 12}, "x": {"type": "number", "minimum": 0, "maximum": 1}, "y": {"type": "number", "minimum": 0, "maximum": 1}}, "required": ["id", "label", "x", "y"]}},
            "requiredConnections": {"type": "array", "maxItems": 12, "items": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "string", "maxLength": 8}}},
        }, "required": ["type", "instruction", "points", "requiredConnections"]},
        "chapter": {"type": "string", "maxLength": 80},
        "knowledgePoint": {"type": "string", "maxLength": 120},
    },
    "required": ["schemaVersion", "questionType", "correctAnswer", "correctAnswers", "blanks", "answerSpec", "interaction", "chapter", "knowledgePoint"],
}


TUTOR_SCRIPT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "schemaVersion": {"type": "string", "maxLength": 40},
        "lessonSteps": {"type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "object", "additionalProperties": False, "properties": {"title": {"type": "string", "maxLength": 40}, "text": {"type": "string", "maxLength": 200}, "speechText": {"type": "string", "maxLength": 200}}, "required": ["title", "text", "speechText"]}},
        "guideCards": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "object", "additionalProperties": False, "properties": {"stuckAt": {"type": "string", "maxLength": 100}, "knowledge": _string_list(4, 40), "hint": {"type": "string", "maxLength": 120}, "question": {"type": "string", "maxLength": 120}}, "required": ["stuckAt", "knowledge", "hint", "question"]}},
    },
    "required": ["schemaVersion", "lessonSteps", "guideCards"],
}


VERIFICATION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "schemaVersion": {"type": "string", "maxLength": 40},
        "status": {"type": "string", "enum": ["verified", "conflict", "needs_review"]},
        "solverAgreement": {"type": "boolean"},
        "sourceAnswer": {"type": "string", "maxLength": 160},
        "conflicts": _string_list(12, 240),
        "checks": _string_list(12, 160),
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "needsHumanReview": {"type": "boolean"},
    },
    "required": ["schemaVersion", "status", "solverAgreement", "sourceAnswer", "conflicts", "checks", "confidence", "needsHumanReview"],
}
