"""Contracts for programmable lessons and persisted learning activity."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


LessonBlockType = Literal[
    "markdown",
    "formula",
    "diagram",
    "animation",
    "annotation",
    "quiz",
    "hint",
]


class LessonBlock(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    type: LessonBlockType
    title: str = Field(default="", max_length=160)
    payload: dict[str, Any] = Field(default_factory=dict)


class LessonDocument(BaseModel):
    lessonId: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    version: int = Field(default=1, ge=1)
    status: Literal["draft", "review", "published", "archived"] = "draft"
    sourceUploadId: str | None = Field(default=None, max_length=64)
    knowledgePoints: list[str] = Field(default_factory=list, max_length=32)
    blocks: list[LessonBlock] = Field(default_factory=list, max_length=200)


class LearningSessionCreate(BaseModel):
    learnerId: str = Field(default="local-demo", min_length=1, max_length=128)
    lessonId: str = Field(min_length=1, max_length=128)


class ExerciseAttemptCreate(BaseModel):
    questionId: str = Field(min_length=1, max_length=128)
    knowledgePoint: str = Field(min_length=1, max_length=160)
    response: dict[str, Any] = Field(default_factory=dict)
    assessment: Literal["correct", "partial", "incorrect"]
    hintLevel: int = Field(default=0, ge=0, le=10)
    durationMs: int = Field(default=0, ge=0, le=3_600_000)


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
                "action": step.get("action", "show-base"),
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
        status="review" if question.get("publicationStatus") == "needs_review" else "published",
        sourceUploadId=source_upload_id,
        knowledgePoints=[str(question.get("knowledgePoint") or title)],
        blocks=blocks,
    )
    return document.model_dump()
