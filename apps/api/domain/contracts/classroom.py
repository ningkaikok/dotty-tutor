"""班级、成员和作业指派的 HTTP 输入契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    subject: str = Field(default="数学", min_length=1, max_length=80)
    gradeBand: str = Field(default="初中", min_length=1, max_length=80)


class ClassMemberCreate(BaseModel):
    learnerId: str = Field(min_length=1, max_length=128)
    displayName: str = Field(min_length=1, max_length=160)


class AssignmentCreate(BaseModel):
    # Direct assignment creation is intentionally impossible; confirmation of
    # an analyzed plan is the only write path.
    planId: str = Field(min_length=1, max_length=64)
    publicationId: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=200)
    dueAt: float | None = Field(default=None, ge=0)
    sourceFingerprint: str = Field(min_length=32, max_length=128)
    confirmWarnings: bool = False


class AssignmentPlanCreate(BaseModel):
    publicationId: str = Field(min_length=1, max_length=64)


class TeacherReviewCreate(BaseModel):
    """Append one teacher decision without changing the original learning evidence."""

    learnerId: str = Field(min_length=1, max_length=128)
    questionId: str | None = Field(default=None, max_length=128)
    knowledgePointId: str | None = Field(default=None, max_length=64)
    action: Literal["reviewed", "overturned", "mastery_override"]
    masteryScore: float | None = Field(default=None, ge=0, le=1)
    correctedAssessment: Literal["correct", "partial", "incorrect"] | None = None
    note: str | None = Field(default=None, max_length=500)
