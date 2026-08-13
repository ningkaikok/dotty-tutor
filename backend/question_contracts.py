"""Question and tutoring API contracts shared by the application layers.

Keeping the generated-question schema here prevents the HTTP entrypoint from
also being the source of truth for model output and frontend contracts.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from model_runtime import Provider
from ocr_runtime import OcrProvider


QuestionType = Literal[
    "choice",
    "multi-select",
    "true-false",
    "short-answer",
    "fill-blank",
    "numeric",
    "draw-line",
]

CANVAS_ACTIONS = ["show-base", "show-point-p", "show-triangles", "show-bisector"]

QUESTION = {
    "id": "geometry-perpendicular-bisector",
    "questionType": "short-answer",
    "correctAnswer": "",
    "interaction": {"type": "none", "instruction": "", "points": [], "requiredConnections": []},
    "chapter": "动点轨迹",
    "knowledgePoint": "到两个定点距离相等的点",
    "prompt": "已知 A、B 是两个定点，点 P 满足 PA = PB。点 P 的运动轨迹是什么？",
    "givens": ["PA = PB", "M 是 AB 的中点"],
}

LESSON_STEPS = [
    {
        "id": "points",
        "title": "建立已知条件",
        "text": "固定点 A、B，M 是 AB 的中点。",
        "speechText": "先画出两个固定点 A 和 B，并标出线段 AB 的中点 M。",
        "action": "show-base",
    },
    {
        "id": "equal-distance",
        "title": "加入动点 P",
        "text": "取满足 PA = PB 的点 P，并连接 PA、PB。",
        "speechText": "现在取一点 P，使它到 A、B 两点的距离相等。",
        "action": "show-point-p",
    },
    {
        "id": "triangles",
        "title": "比较两个三角形",
        "text": "PA = PB，AM = BM，PM 为公共边。",
        "speechText": "比较三角形 PAM 和 PBM，它们有三组对应边相等。",
        "action": "show-triangles",
    },
    {
        "id": "conclusion",
        "title": "得到轨迹",
        "text": "PM 垂直 AB；所有这样的 P 都在 AB 的垂直平分线上。",
        "speechText": "因此 PM 垂直于 AB，点 P 的轨迹就是线段 AB 的垂直平分线。",
        "action": "show-bisector",
    },
]

GUIDE_CARDS = [
    {
        "level": 0,
        "stuckAt": "还没有把“到两点距离相等”转化为可以证明的几何关系。",
        "knowledge": ["等距", "中点", "全等三角形"],
        "hint": "先连接 PA、PB，再利用 M 是 AB 的中点。",
        "question": "比较三角形 PAM 和 PBM，你能找到哪三组相等的边？",
        "canvasAction": "show-triangles",
    },
    {
        "level": 1,
        "stuckAt": "已经找到相等的边，但还没有使用全等三角形。",
        "knowledge": ["SSS 全等", "对应角相等"],
        "hint": "PA = PB、AM = BM，另外 PM 是两个三角形的公共边。",
        "question": "两个三角形全等后，∠PMA 和 ∠PMB 有什么关系？",
        "canvasAction": "show-triangles",
    },
    {
        "level": 2,
        "stuckAt": "已经证明两个邻角相等，还差最后的垂直关系。",
        "knowledge": ["邻补角", "垂直", "垂直平分线"],
        "hint": "∠PMA 与 ∠PMB 相等，并且它们组成一个平角。",
        "question": "两个相等的邻补角分别是多少度？这说明 PM 与 AB 有什么关系？",
        "canvasAction": "show-bisector",
    },
]


def _choice_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": 6,
        "items": {"type": "string", "maxLength": 120},
    }


LESSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "chapter": {"type": "string", "maxLength": 30},
        "knowledgePoint": {"type": "string", "maxLength": 50},
        "questionNumber": {"type": "string", "maxLength": 20},
        "questionType": {"type": "string", "enum": list(QuestionType.__args__)},
        "selectionMode": {"type": "string", "enum": ["single", "multiple"]},
        "prompt": {"type": "string", "maxLength": 800},
        "correctAnswer": {"type": "string", "maxLength": 120},
        "correctAnswers": _choice_schema(),
        "blanks": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "maxLength": 24},
                    "label": {"type": "string", "maxLength": 30},
                    "answerType": {"type": "string", "enum": ["text", "numeric", "expression"]},
                    "correctAnswers": _choice_schema(),
                    "tolerance": {"type": "number", "minimum": 0},
                    "unit": {"type": "string", "maxLength": 20},
                },
                "required": ["id", "label", "answerType", "correctAnswers", "tolerance", "unit"],
            },
        },
        "answerSpec": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "answerType": {"type": "string", "enum": ["numeric", "expression"]},
                "expected": {"type": "string", "maxLength": 120},
                "accepted": _choice_schema(),
                "tolerance": {"type": "number", "minimum": 0},
                "unit": {"type": "string", "maxLength": 20},
            },
            "required": ["answerType", "expected", "accepted", "tolerance", "unit"],
        },
        "interaction": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "type": {"type": "string", "enum": ["none", "draw-line"]},
                "instruction": {"type": "string", "maxLength": 160},
                "points": {
                    "type": "array",
                    "maxItems": 12,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "id": {"type": "string", "maxLength": 8},
                            "label": {"type": "string", "maxLength": 12},
                            "x": {"type": "number", "minimum": 0, "maximum": 1},
                            "y": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                        "required": ["id", "label", "x", "y"],
                    },
                },
                "requiredConnections": {
                    "type": "array",
                    "maxItems": 12,
                    "items": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "string", "maxLength": 8}},
                },
            },
            "required": ["type", "instruction", "points", "requiredConnections"],
        },
        "givens": {"type": "array", "maxItems": 5, "items": {"type": "string", "maxLength": 80}},
        "options": _choice_schema(),
        "imageReferences": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 160}},
        "lessonSteps": {
            "type": "array",
            "minItems": 4,
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string", "maxLength": 20},
                    "text": {"type": "string", "maxLength": 120},
                    "speechText": {"type": "string", "maxLength": 120},
                },
                "required": ["title", "text", "speechText"],
            },
        },
        "guideCards": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "stuckAt": {"type": "string", "maxLength": 80},
                    "knowledge": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 30}},
                    "hint": {"type": "string", "maxLength": 100},
                    "question": {"type": "string", "maxLength": 100},
                },
                "required": ["stuckAt", "knowledge", "hint", "question"],
            },
        },
    },
    "required": [
        "chapter", "knowledgePoint", "questionNumber", "questionType", "selectionMode", "prompt",
        "correctAnswer", "correctAnswers", "blanks", "answerSpec", "interaction", "givens", "options", "imageReferences",
        "lessonSteps", "guideCards",
    ],
}

HELP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "assessment": {"type": "string", "enum": ["correct", "partial", "incorrect"]},
        "reply": {"type": "string", "maxLength": 220},
        "stuckAt": {"type": "string", "maxLength": 100},
        "knowledge": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 30}},
        "hint": {"type": "string", "maxLength": 120},
        "question": {"type": "string", "maxLength": 120},
        "canvasAction": {"type": "string", "enum": CANVAS_ACTIONS},
        "misconception": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "hypothesis": {"type": "string", "maxLength": 160},
                "evidence": {"type": "string", "maxLength": 240},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "needsConfirmation": {"type": "boolean"},
            },
            # Codex strict schema 要求 required 覆盖 properties 中的每一个字段。
            "required": ["hypothesis", "evidence", "confidence", "needsConfirmation"],
        },
    },
    "required": [
        "assessment", "reply", "stuckAt", "knowledge", "hint", "question",
        "canvasAction", "misconception",
    ],
}


class HelpRequest(BaseModel):
    questionId: str
    publicationId: str | None = Field(default=None, max_length=128)
    studentInput: str = Field(default="", max_length=1_000)
    hintLevel: int = Field(default=0, ge=0, le=3)
    language: Literal["zh", "en"] = "zh"
    mode: Literal["answer", "help"] = "help"
    interactionResult: dict[str, Any] = Field(default_factory=dict)


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2_000)
    speaker: str = Field(default="Serena", max_length=40)
    instruct: str = Field(
        default="用耐心、清晰、自然的中文老师语气朗读，语速稍慢，重点处有轻微停顿。",
        max_length=200,
    )


class TutorReply(BaseModel):
    reply: str
    guideContext: dict
    nextHintLevel: int
    canvasAction: str
    source: Literal["stored-guide-card", "answer-check", "model-generated"]
    modelRun: dict[str, Any] = Field(default_factory=dict)


class ModelSelectionRequest(BaseModel):
    provider: Provider
    model: str = Field(min_length=1, max_length=100)


class OcrSelectionRequest(BaseModel):
    provider: OcrProvider


class PdfUploadInitRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0, le=500 * 1024 * 1024)
    contentType: str = "application/pdf"
    chunkSize: int = Field(default=5 * 1024 * 1024, ge=1024, le=5 * 1024 * 1024)
    totalChunks: int = Field(gt=0, le=200)
    sourceText: str = Field(default="", max_length=20_000)
