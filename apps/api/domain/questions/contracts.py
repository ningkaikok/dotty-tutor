"""Question and tutoring API contracts shared by the application layers.

Keeping the generated-question schema here prevents the HTTP entrypoint from
also being the source of truth for model output and frontend contracts.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from infrastructure.runtime.model_runtime import Provider
from infrastructure.runtime.ocr_runtime import OcrProvider

QuestionType = Literal[
    "choice",
    "multi-select",
    "true-false",
    "short-answer",
    "fill-blank",
    "numeric",
    "draw-line",
]


def _choice_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": 6,
        "items": {"type": "string", "maxLength": 120},
    }


def _blank_item_schema() -> dict[str, Any]:
    return {
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
    }


def _answer_spec_schema() -> dict[str, Any]:
    return {
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
    }


def _interaction_schema() -> dict[str, Any]:
    return {
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
    }


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    """Widen an object schema's ``type`` to allow ``null`` without touching its shape."""
    widened = dict(schema)
    widened["type"] = [schema["type"], "null"]
    return widened


SUB_QUESTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "minLength": 1, "maxLength": 40},
        "label": {"type": "string", "minLength": 1, "maxLength": 20},
        "prompt": {"type": "string", "minLength": 1, "maxLength": 800},
        "questionType": {"type": "string", "enum": list(QuestionType.__args__)},
        "evaluation": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "mode": {"type": "string", "enum": ["deterministic", "tutor"]},
                "reason": {"type": ["string", "null"], "maxLength": 160},
            },
            "required": ["mode", "reason"],
        },
        "correctAnswer": {"type": ["string", "null"], "maxLength": 120},
        "correctAnswers": {"type": ["array", "null"], "items": {"type": "string", "maxLength": 120}},
        "options": {"type": ["array", "null"], "items": {"type": "string", "maxLength": 120}},
        # 与 LESSON_SCHEMA 顶层 blanks/answerSpec/interaction 共用同一份对象形状（经由
        # 上面的工厂函数），否则两处手写的 schema 会各自漂移；此前这里只写了裸 "object"，
        # 没有 additionalProperties，触发 Codex 结构化输出 API 的 400（结构化题型生成
        # 因此每次都静默回退到 mock 桩，题型固定为 short-answer）。
        "blanks": {"type": ["array", "null"], "maxItems": 8, "items": _blank_item_schema()},
        "answerSpec": _nullable(_answer_spec_schema()),
        "interaction": _nullable(_interaction_schema()),
        # contentBlocks 只是模型必须提交的占位字段，真正展示给学生的版本由
        # build_question_content_blocks 在生成后重新构建（见 domain/questions/pipeline.py），
        # 所以这里不需要真实结构，只需要满足严格 schema 要求的合法空对象。
        "contentBlocks": {"type": ["array", "null"], "items": {"type": "object", "additionalProperties": False}},
    },
    "required": [
        "id", "label", "prompt", "questionType", "evaluation", "correctAnswer",
        "correctAnswers", "options", "blanks", "answerSpec", "interaction", "contentBlocks",
    ],
}

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
        "blanks": {"type": "array", "maxItems": 8, "items": _blank_item_schema()},
        "answerSpec": _answer_spec_schema(),
        "interaction": _interaction_schema(),
        "subQuestions": {
            "type": "array",
            "maxItems": 12,
            "items": SUB_QUESTION_SCHEMA,
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
        "subQuestions", "lessonSteps", "guideCards",
    ],
}

# The planner owns the topic/evidence boundary; lesson generation only fills
# one new lesson per returned topic. Keeping this as a separate contract avoids
# accidentally exposing learner-level planning fields to the model.
PERSONALIZED_ASSIGNMENT_SCHEMA_VERSION = "personalized-assignment-schema-v1"
PERSONALIZED_ASSIGNMENT_PROMPT_VERSION = "personalized-assignment-v1"
PERSONALIZED_ASSIGNMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "questions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "planningTopicKey": {"type": "string", "minLength": 1, "maxLength": 160},
                    "lesson": LESSON_SCHEMA,
                },
                "required": ["planningTopicKey", "lesson"],
            },
        },
    },
    "required": ["questions"],
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
                "category": {
                    "type": "string",
                    "enum": ["concept", "reading", "calculation", "missing_step", "unknown", "careless"],
                },
            },
            # Codex strict schema 要求 required 覆盖 properties 中的每一个字段。
            "required": ["hypothesis", "evidence", "confidence", "needsConfirmation", "category"],
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


# 分块上传的限额定义在这里，因为这里才是真正执行它们的地方（Pydantic 校验）。
# 路由模块曾经另存一份同名常量，但从不被读取——改那份不会生效，只会误导。
PDF_MAX_UPLOAD_BYTES = 500 * 1024 * 1024
PDF_MAX_CHUNK_BYTES = 5 * 1024 * 1024
PDF_MIN_CHUNK_BYTES = 1024
PDF_MAX_CHUNKS = 200


class PdfUploadInitRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0, le=PDF_MAX_UPLOAD_BYTES)
    contentType: str = "application/pdf"
    chunkSize: int = Field(
        default=PDF_MAX_CHUNK_BYTES, ge=PDF_MIN_CHUNK_BYTES, le=PDF_MAX_CHUNK_BYTES,
    )
    totalChunks: int = Field(gt=0, le=PDF_MAX_CHUNKS)
    sourceText: str = Field(default="", max_length=20_000)
