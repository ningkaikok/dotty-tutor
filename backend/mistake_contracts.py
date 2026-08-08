"""Validated contracts for mistake capture and student confirmation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


MistakeErrorReason = Literal[
    "concept",
    "reading",
    "calculation",
    "missing_step",
    "unknown",
    "careless",
]


class MistakeConfirmation(BaseModel):
    prompt: str = Field(min_length=1, max_length=8_000)
    originalAnswer: str = Field(default="", max_length=2_000)
    subject: str = Field(default="数学", min_length=1, max_length=80)
    gradeBand: str = Field(default="初中", min_length=1, max_length=80)
    chapter: str = Field(min_length=1, max_length=160)
    knowledgePoint: str = Field(min_length=1, max_length=200)
    errorReason: MistakeErrorReason
    notes: str = Field(default="", max_length=2_000)


class MistakeArchiveRequest(BaseModel):
    archived: bool = True
