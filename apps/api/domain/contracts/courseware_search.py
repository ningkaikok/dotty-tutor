"""Publication-bound textbook evidence and grounded explanation contracts."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from domain.contracts.courseware import CoursewareSourceRef


class CoursewareSourceEvidence(BaseModel):
    documentId: str
    coursewareId: str
    publicationId: str
    publicationVersion: int
    sourceRevisionId: str
    sourcePage: int
    coursewarePageId: str
    title: str
    excerpt: str
    sourceRef: CoursewareSourceRef
    previewUrl: str
    rank: float = 0


class CoursewareSourceSearchResponse(BaseModel):
    items: list[CoursewareSourceEvidence]


class CoursewareSourceExplainRequest(BaseModel):
    publicationId: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=1000)

    model_config = ConfigDict(extra="forbid")


class CoursewareSourceExplanation(BaseModel):
    status: Literal["answered", "insufficient_evidence", "unavailable"]
    explanation: str
    evidence: list[CoursewareSourceEvidence]
    run: dict[str, Any] | None = None
