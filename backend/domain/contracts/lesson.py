"""可编程课程、互动试卷和持久化学习活动的共享契约。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from domain.tutoring.checks import safe_canvas_action


LessonBlockType = Literal[
    "markdown",
    "formula",
    "diagram",
    "animation",
    "annotation",
    "quiz",
    "hint",
]

LessonStatus = Literal["draft", "in_review", "review", "published", "archived"]
PublicationStatus = Literal["draft", "in_review", "published", "archived"]


class LessonBlock(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    type: LessonBlockType
    title: str = Field(default="", max_length=160)
    payload: dict[str, Any] = Field(default_factory=dict)


class LessonDocument(BaseModel):
    lessonId: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    version: int = Field(default=1, ge=1)
    status: LessonStatus = "draft"
    sourceUploadId: str | None = Field(default=None, max_length=64)
    knowledgePoints: list[str] = Field(default_factory=list, max_length=32)
    blocks: list[LessonBlock] = Field(default_factory=list, max_length=200)
    # 在可渲染内容块旁保留原题，使学生端与工作台复用同一套作答组件。
    # 发布版本中的 payload 不可变，但它只是版本快照，不是生成内容的第二真相来源。
    questionPayload: dict[str, Any] = Field(default_factory=dict)
    guideCards: list[dict[str, Any]] = Field(default_factory=list, max_length=32)


class PublicationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    sourceUploadId: str | None = Field(default=None, max_length=64)
    lessonIds: list[str] = Field(min_length=1, max_length=100)


class PublicationStatusUpdate(BaseModel):
    status: PublicationStatus


class LearningSessionCreate(BaseModel):
    learnerId: str = Field(default="local-demo", min_length=1, max_length=128)
    # Sessions always target an immutable published paper, never an individual lesson.
    publicationId: str = Field(min_length=1, max_length=128)


class ExerciseAttemptCreate(BaseModel):
    attemptId: str | None = Field(default=None, min_length=1, max_length=64)
    questionId: str = Field(min_length=1, max_length=128)
    knowledgePoint: str = Field(min_length=1, max_length=160)
    response: dict[str, Any] = Field(default_factory=dict)
    assessment: Literal["correct", "partial", "incorrect"]
    hintLevel: int = Field(default=0, ge=0, le=10)
    durationMs: int = Field(default=0, ge=0, le=3_600_000)
    # 浏览器携带原始作答时间，避免离线补传把旧练习误记成刚完成。
    createdAt: float = Field(ge=0)


class LearningSyncCreate(BaseModel):
    attempts: list[ExerciseAttemptCreate] = Field(min_length=1, max_length=100)


def lesson_document_from_payload(
    payload: dict[str, Any],
    *,
    source_upload_id: str | None = None,
    guide_cards: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Adapt the current question payload to the versioned lesson runtime."""
    question = payload.get("question", {})
    lesson_id = str(question.get("id") or "lesson")[:128]
    title = str(question.get("knowledgePoint") or question.get("chapter") or "互动课程")[:200]
    blocks: list[dict[str, Any]] = []

    for index, step in enumerate(payload.get("lessonSteps", [])):
        blocks.append({
            "id": str(step.get("id") or f"step-{index + 1}"),
            "type": "diagram",
            "title": str(step.get("title") or f"步骤 {index + 1}"),
            "payload": {
                "renderer": "geometry",
                # 当前模型内容仍需经过安全约束，避免普通题目在学生端显示
                # 三角形或垂直平分线等不匹配的几何动作。
                "action": safe_canvas_action(question, step.get("action", "show-base")),
                "text": step.get("text", ""),
                "speechText": step.get("speechText", ""),
            },
        })

    blocks.append({
        "id": f"{lesson_id}-quiz",
        "type": "quiz",
        "title": "课后练习",
        "payload": {"questionId": lesson_id},
    })
    for index, card in enumerate(guide_cards or []):
        blocks.append({
            "id": f"{lesson_id}-hint-{index + 1}",
            "type": "hint",
            "title": f"提示 {index + 1}",
            "payload": {
                "level": index,
                "hint": card.get("hint", ""),
                "question": card.get("question", ""),
            },
        })

    document = LessonDocument(
        lessonId=lesson_id,
        title=title,
        # 生成只产生可审核草稿；发布必须由内容工作台显式触发，学生不会看到尚未通过质量门禁的内容。
        status="in_review" if question.get("publicationStatus") == "needs_review" else "draft",
        sourceUploadId=source_upload_id,
        knowledgePoints=[str(question.get("knowledgePoint") or title)],
        blocks=blocks,
        questionPayload=payload,
        guideCards=guide_cards or [],
    )
    return document.model_dump()
