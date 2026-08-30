"""班级、成员和作业指派的 HTTP 输入契约。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    subject: str = Field(default="数学", min_length=1, max_length=80)
    gradeBand: str = Field(default="初中", min_length=1, max_length=80)


class ClassMemberCreate(BaseModel):
    learnerId: str = Field(min_length=1, max_length=128)
    displayName: str = Field(min_length=1, max_length=160)


class AssignmentCreate(BaseModel):
    publicationId: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=200)
    dueAt: float | None = Field(default=None, ge=0)
