"""Chapter-backed navigation and learner-owned reading positions.

Opening a page is a location marker, never evidence of mastery. Activity
summaries are computed from the existing immutable attempt records.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CoursewareReadingPosition(BaseModel):
    coursewareId: str
    publicationId: str
    pageId: str
    version: int = Field(ge=1)
    updatedAt: float
    available: bool = True


class CoursewareUnitLesson(BaseModel):
    coursewareId: str
    title: str
    order: int
    pageStart: int
    pageEnd: int
    durationMinutes: int
    status: str
    currentBatch: bool
    publicationId: str | None = None
    publicationVersion: int | None = None
    sourceRevisionId: str
    activityCount: int = 0
    answeredActivityCount: int = 0
    needsCorrectionCount: int = 0
    activityStatus: Literal["not_started", "answered", "needs_correction"] = "not_started"
    progress: CoursewareReadingPosition | None = None


class CoursewareUnit(BaseModel):
    chapterId: str
    title: str
    subject: str
    lessons: list[CoursewareUnitLesson]
    resume: CoursewareReadingPosition | None = None


class CoursewareUnitsResponse(BaseModel):
    items: list[CoursewareUnit]


class CoursewarePositionSave(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publicationId: str = Field(min_length=1, max_length=64)
    pageId: str = Field(min_length=1, max_length=128)
    expectedVersion: int = Field(ge=0)
    commandId: str = Field(min_length=1, max_length=128)
