"""Public, non-sensitive audit response contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RunOperation = Literal[
    "question_repair",
    "question_reocr",
    "batch_regenerate",
    "publication_rereview",
    "initial_batch",
    "stage_rerun",
    "question_manual_edit",
    "question_revision_activate",
]

RevisionSource = Literal["model_generated", "manual_edit"]


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
    # 审校面板据此区分"模型生成/重跑"和"老师手工编辑"；默认值只覆盖迁移前的历史行。
    revisionSource: RevisionSource = "model_generated"
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


class QuestionReviewQueueResponse(BaseModel):
    """题目工作台所需的来源、问题和阶段运行快照。"""

    model_config = ConfigDict(extra="forbid")

    uploadId: str
    sourceQuestionKey: str
    questionPayload: dict[str, Any] | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    issues: list[dict[str, Any]] = Field(default_factory=list)
    stageRuns: list[dict[str, Any]] = Field(default_factory=list)
    # 人工编辑必须绑定这个值做乐观并发；回滚后它可能不是历史列表里的最后一条，
    # 因此前端不能靠"revision 列表最后一项"猜测当前版本，只能读这个字段。
    currentRevisionId: str | None = None


class ReviewQueueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uploadId: str
    items: list[QuestionReviewQueueResponse] = Field(default_factory=list)


class StageRerunResponse(QuestionRegenerationResponse):
    """指定阶段及其下游重跑结果。"""

    stage: str
    stages: list[dict[str, Any]] = Field(default_factory=list)


class QuestionEditResponse(AuditedOperationResponse):
    """人工字段级编辑成功后的稳定 HTTP 契约。"""

    batch: dict[str, Any] | None = None
    questionPayload: dict[str, Any] | None = None
    guideCards: list[dict[str, Any]] = Field(default_factory=list)
    edit: dict[str, Any]
    revision: RevisionSummary | None = None


class QuestionRevisionActivateResponse(AuditedOperationResponse):
    """把题目当前展示版本回滚/指向某条历史 revision 后的稳定 HTTP 契约。"""

    batch: dict[str, Any] | None = None
    questionPayload: dict[str, Any] | None = None
    guideCards: list[dict[str, Any]] = Field(default_factory=list)
    activation: dict[str, Any]
    activatedRevision: RevisionSummary


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


class BackgroundJobSummary(BaseModel):
    """Client-safe snapshot of one durable background job.

    The payload, lease owner and idempotency key are intentionally not exposed:
    they are execution details and may contain source identifiers that the UI
    does not need. ``result`` is the completed operation response.
    """

    jobId: str
    jobType: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    progress: int = Field(ge=0, le=100)
    message: str
    attemptCount: int = Field(ge=0)
    maxAttempts: int = Field(ge=1)
    cancelRequested: bool = False
    lastError: dict[str, Any] | None = None
    result: Any = None
    createdAt: float
    updatedAt: float
    startedAt: float | None = None
    completedAt: float | None = None


class PublicationRevisionResponse(AuditedOperationResponse):
    """整套重新审核创建新发布版本时返回的审计结果。"""

    publication: dict[str, Any]
    questionPayloads: list[dict[str, Any]] = Field(default_factory=list)
    revisions: list[RevisionSummary] = Field(default_factory=list)
