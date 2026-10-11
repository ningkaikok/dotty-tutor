"""HTTP contracts for source-bound lesson video drafts and publication."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class LessonMediaDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapterId: str = Field(min_length=1, max_length=64)
    publicationId: str = Field(min_length=1, max_length=64)
    lessonId: str = Field(min_length=1, max_length=128)
    sourceRevisionId: str = Field(min_length=1, max_length=64)


class LessonMediaSceneEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sceneId: str = Field(min_length=1, max_length=64)
    narration: str = Field(min_length=1, max_length=2_000)


class LessonMediaRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedVersion: int = Field(ge=1)
    sceneEdits: list[LessonMediaSceneEdit] = Field(min_length=1, max_length=8)


class LessonMediaRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedVersion: int = Field(ge=1)


class LessonMediaReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "request_changes"]
    displayName: str | None = Field(default=None, max_length=128)
    note: str = Field(default="", max_length=1_000)


class LessonMediaPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    displayName: str | None = Field(default=None, max_length=128)


class LessonMediaScene(BaseModel):
    sceneId: str
    order: int
    kind: str
    title: str
    narration: str
    sourceRefs: list[dict[str, Any]]


class LessonMediaConsistencyReview(BaseModel):
    passed: bool
    issues: list[dict[str, Any]]
    checkedAt: float
    checks: dict[str, bool]


class LessonMediaResponse(BaseModel):
    mediaId: str
    seriesId: str
    version: int
    chapterId: str
    publicationId: str
    lessonId: str
    sourceRevisionId: str
    title: str
    subject: Literal["math", "english"]
    license: str
    sourceTitle: str | None = None
    sourceUrl: str | None = None
    attributionText: str | None = None
    distributionScope: Literal["student", "local_only"]
    sourceSnapshot: dict[str, Any]
    rendererSnapshot: dict[str, Any]
    scenes: list[LessonMediaScene]
    status: str
    durationSeconds: float | None = None
    artifactSha256: str | None = None
    consistencyReview: LessonMediaConsistencyReview | None = None
    reviews: list[dict[str, Any]] = Field(default_factory=list)
    renderJob: dict[str, Any] | None = None
    createdAt: float
    publishedAt: float | None = None


class LessonMediaListResponse(BaseModel):
    items: list[LessonMediaResponse]


class LessonMediaPublishedItem(BaseModel):
    mediaId: str
    version: int
    title: str
    subject: Literal["math", "english"]
    sourceRevisionId: str
    sourceTitle: str | None = None
    sourceUrl: str | None = None
    attributionText: str | None = None
    durationSeconds: float
    videoUrl: str
    captionsUrl: str
    captionsDownloadUrl: str


class LessonMediaPublishedResponse(BaseModel):
    chapterId: str
    publicationId: str
    lessonId: str
    sourceRevisionId: str
    items: list[LessonMediaPublishedItem]
