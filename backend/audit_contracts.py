"""Public, non-sensitive audit response contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


RunOperation = Literal[
    "question_repair",
    "question_reocr",
    "batch_regenerate",
    "publication_rereview",
    "initial_batch",
]


class RunSummary(BaseModel):
    runId: str
    operation: RunOperation
    scope: str
    targetUploadId: str | None = None
    targetQuestionKey: str | None = None
    targetPublicationId: str | None = None
    status: Literal["running", "succeeded", "failed"]
    config: dict[str, Any]
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    startedAt: float
    completedAt: float | None = None


class RevisionSummary(BaseModel):
    revisionId: str
    uploadId: str
    sourceQuestionKey: str
    revisionNumber: int
    operation: RunOperation
    previousRevisionId: str | None = None
    runId: str
    createdAt: float


class AuditedOperationResponse(BaseModel):
    """所有真实重跑操作都必须返回不可伪造的运行快照摘要。"""

    run: RunSummary


class QuestionRegenerationResponse(AuditedOperationResponse):
    """单题修复或重新 OCR 的稳定 HTTP 契约。"""

    batch: dict[str, Any] | None = None
    questionPayload: dict[str, Any] | None = None
    guideCards: list[dict[str, Any]] = Field(default_factory=list)
    ocrRun: dict[str, Any] | None = None
    modelRun: dict[str, Any] | None = None
    reviewRun: dict[str, Any] | None = None
    regeneration: dict[str, Any]
    revision: RevisionSummary | None = None


class BatchProcessResponse(BaseModel):
    """Batch endpoint also serves an idempotent read from an existing cache.

    The cache branch is not an operation and therefore intentionally has no
    fabricated run. Forced batch regeneration still returns a completed run.
    """

    run: RunSummary | None = None
    batch: dict[str, Any]
    questionPayload: dict[str, Any] | None = None
    questionPayloads: list[dict[str, Any]] = Field(default_factory=list)
    guideCards: list[list[dict[str, Any]]] = Field(default_factory=list)
    ocrRun: dict[str, Any] | None = None
    modelRun: dict[str, Any] | None = None
    modelRuns: list[dict[str, Any]] = Field(default_factory=list)
    reviewRun: dict[str, Any] | None = None
    reviewRuns: list[dict[str, Any]] = Field(default_factory=list)
    revisions: list[RevisionSummary] = Field(default_factory=list)


class PublicationRevisionResponse(AuditedOperationResponse):
    """整套重新审核创建新发布版本时返回的审计结果。"""

    publication: dict[str, Any]
    questionPayloads: list[dict[str, Any]] = Field(default_factory=list)
    revisions: list[RevisionSummary] = Field(default_factory=list)
