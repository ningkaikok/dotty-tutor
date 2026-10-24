"""Contracts for explicitly requested whole-book courseware production."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from domain.contracts.courseware import (
    CoursewareObjective,
    CoursewareSessionPlan,
    CoursewareSourceRef,
    CoursewareSubject,
)

MAX_WHOLE_BOOK_PAGES = 1000
MAX_WHOLE_BOOK_BATCH_PAGES = 30
MAX_WHOLE_BOOK_BATCHES = (MAX_WHOLE_BOOK_PAGES + MAX_WHOLE_BOOK_BATCH_PAGES - 1) // MAX_WHOLE_BOOK_BATCH_PAGES
MAX_GENERATED_PAGES_PER_BATCH = 45


class WholeBookStartRequest(BaseModel):
    """Book-level settings; physical page ranges are always server-derived."""

    mode: Literal["whole_book"]
    confirmedMaterialKind: Literal["textbook"] | None = None
    subject: CoursewareSubject
    title: str | None = Field(default=None, min_length=1, max_length=200)
    sourceTitle: str | None = Field(default=None, max_length=200)
    sourceUrl: str | None = Field(default=None, max_length=500)
    license: str | None = Field(default=None, max_length=256)
    attributionText: str | None = Field(default=None, max_length=500)
    distributionScope: Literal["private", "classroom", "public"] = "private"

    model_config = ConfigDict(extra="forbid")


class WholeBookControlRequest(BaseModel):
    commandId: str = Field(min_length=1, max_length=128)

    model_config = ConfigDict(extra="forbid")


class WholeBookBatchView(BaseModel):
    """Durable progress for one source-derived range of up to 30 PDF pages."""

    batchIndex: int = Field(ge=0, le=MAX_WHOLE_BOOK_BATCHES - 1)
    title: str = Field(min_length=1, max_length=300)
    pageStart: int = Field(ge=1, le=MAX_WHOLE_BOOK_PAGES)
    pageEnd: int = Field(ge=1, le=MAX_WHOLE_BOOK_PAGES)
    status: Literal[
        "queued", "source_preparation", "planning", "generating", "checking",
        "repairing", "published", "completed", "needs_attention", "failed", "source_changed",
    ]
    message: str = Field(default="", max_length=1000)
    chapterId: str | None = None
    coursewareIds: list[str] = Field(default_factory=list, max_length=12)
    publicationIds: list[str] = Field(default_factory=list, max_length=12)
    skippedPages: list[dict[str, str | int]] = Field(default_factory=list, max_length=MAX_WHOLE_BOOK_BATCH_PAGES)
    error: dict[str, str | bool] | None = None


class WholeBookRunView(BaseModel):
    """A read-only, resumable view of a whole-book run."""

    runId: str
    uploadId: str
    filename: str
    status: Literal[
        "queued", "running", "waiting_for_source", "paused", "needs_attention", "source_changed", "completed",
    ]
    message: str = Field(default="", max_length=1000)
    totalPages: int = Field(ge=1, le=MAX_WHOLE_BOOK_PAGES)
    completedPages: int = Field(ge=0, le=MAX_WHOLE_BOOK_PAGES)
    completedBatches: int = Field(ge=0, le=MAX_WHOLE_BOOK_BATCHES)
    batchCount: int = Field(ge=1, le=MAX_WHOLE_BOOK_BATCHES)
    currentBatchIndex: int | None = Field(default=None, ge=0, le=MAX_WHOLE_BOOK_BATCHES - 1)
    batches: list[WholeBookBatchView] = Field(min_length=1, max_length=MAX_WHOLE_BOOK_BATCHES)
    canPause: bool = False
    canResume: bool = False
    createdAt: float
    updatedAt: float


class WholeBookPageDisposition(BaseModel):
    """Require every original page to be covered or explicitly skipped."""

    page: int = Field(ge=1, le=MAX_WHOLE_BOOK_PAGES)
    disposition: Literal["covered", "skipped"]
    reason: Literal[
        "cover_or_title", "table_of_contents", "copyright_or_legal",
        "blank_page", "index_or_glossary",
    ] | None = None
    note: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def require_skip_reason(self) -> WholeBookPageDisposition:
        if self.disposition == "skipped" and (self.reason is None or not self.note.strip()):
            raise ValueError("跳过原页必须附带类型和说明")
        if self.disposition == "covered" and self.reason is not None:
            raise ValueError("已覆盖原页不能同时声明跳过原因")
        return self


class WholeBookPlanDraftOutput(BaseModel):
    """Whole-book-only model output with a bounded and auditable page ledger."""

    objectives: list[CoursewareObjective] = Field(default_factory=list, max_length=12)
    sessions: list[CoursewareSessionPlan] = Field(default_factory=list, max_length=12)
    coverageMap: dict[str, list[CoursewareSourceRef]] = Field(default_factory=dict, max_length=12)
    pageDispositions: list[WholeBookPageDisposition] = Field(min_length=1, max_length=MAX_WHOLE_BOOK_BATCH_PAGES)

    @field_validator("coverageMap", mode="before")
    @classmethod
    def decode_coverage_entries(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        result: dict[str, object] = {}
        for entry in value:
            if not isinstance(entry, dict) or set(entry) != {"objectiveId", "sourceRefs"}:
                raise ValueError("coverageMap 条目必须包含 objectiveId 与 sourceRefs")
            key = entry["objectiveId"]
            if not isinstance(key, str) or key in result:
                raise ValueError("coverageMap 目标 ID 必须是唯一文本")
            result[key] = entry["sourceRefs"]
        return result

    @model_validator(mode="after")
    def content_matches_disposition(self) -> WholeBookPlanDraftOutput:
        has_content = bool(self.objectives or self.sessions or self.coverageMap)
        if has_content and not (self.objectives and self.sessions and self.coverageMap):
            raise ValueError("教程内容必须同时包含目标、课时和来源覆盖")
        if not has_content and any(item.disposition != "skipped" for item in self.pageDispositions):
            raise ValueError("空教程只能用于全部明确跳过的非教学页段")
        return self
