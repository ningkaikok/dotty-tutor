"""Pydantic response models for FastAPI's ``response_model=``.

These give every endpoint a validated shape and a precise OpenAPI schema
instead of a bare ``dict``. Model-generated and OCR-derived content (question
bodies, content blocks, review notes) varies more than hand-written request
payloads, so the models covering that content declare the fields callers
actually rely on and allow unknown extra fields through rather than silently
dropping them — validated where we know the shape, permissive at the edges
where the shape is inherently dynamic.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ModelRun(BaseModel):
    requestedProvider: str
    provider: str
    model: str
    fallback: bool
    error: str | None = None


class OcrRun(BaseModel):
    model_config = ConfigDict(extra="allow")

    requestedProvider: str
    provider: str
    mode: str
    fallback: bool
    output: str
    error: str | None = None
    sourceArtifactUrl: str | None = None
    promptArtifactUrl: str | None = None


class TextReview(BaseModel):
    model_config = ConfigDict(extra="allow")

    verdict: str
    corrections: list[dict[str, Any]] = []
    issues: list[str] = []
    confidence: float


class VisionReview(BaseModel):
    model_config = ConfigDict(extra="allow")

    correctAnswer: str | None = None
    imageAssessments: list[dict[str, Any]] = []
    issues: list[str] = []
    confidence: float


class ReviewRun(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: Literal["reviewed", "needs_review"]
    needsHumanReview: bool
    text: TextReview
    vision: VisionReview
    textModelRun: ModelRun
    visionModelRun: ModelRun


class QualityReport(BaseModel):
    status: Literal["ready", "needs_review"]
    errors: list[str] = []
    warnings: list[str] = []
    validatorVersion: str
    validatedAt: float


class Question(BaseModel):
    """Question fields the frontend and pipeline rely on.

    Question generation, review and normalization each add or adjust fields
    (``sourceQuestionKey``, ``visualContext``, ...), so unknown keys pass
    through instead of being dropped.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    questionType: str | None = None
    selectionMode: Literal["single", "multiple"] | None = None
    chapter: str
    knowledgePoint: str
    questionNumber: str | None = None
    prompt: str
    correctAnswer: str | None = None
    correctAnswers: list[str] | None = None
    blanks: list[dict[str, Any]] | None = None
    answerSpec: dict[str, Any] | None = None
    interaction: dict[str, Any] | None = None
    givens: list[str] = []
    options: list[str] | None = None
    imageUrls: list[str] | None = None
    optionImageUrls: list[str] | None = None
    contentBlocks: list[dict[str, Any]] | None = None
    publicationStatus: Literal["ready", "needs_review"] | None = None
    sourceArtifactUrl: str | None = None
    promptArtifactUrl: str | None = None
    sourceBatchId: str | None = None


class LessonStep(BaseModel):
    id: str
    title: str
    text: str
    speechText: str
    action: str


class QuestionPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    question: Question
    lessonSteps: list[LessonStep] = []
    architecture: dict[str, str] = {}
    modelRun: ModelRun
    review: ReviewRun | None = None
    quality: QualityReport | None = None


class ImportStage(BaseModel):
    id: str
    label: str
    status: Literal["done"]


class ExtractionInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    chapter: str
    knowledgePoint: str
    questionCount: int
    questionLimit: int | None = None
    formulaCount: int
    guideCardCount: int
    pageCount: int | None = None
    batchCount: int | None = None
    confidence: float
    mode: str


class BatchInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    startPage: int
    endPage: int
    pageCount: int
    status: Literal["processed", "queued", "failed"]
    error: str | None = None


class TextbookImportResult(BaseModel):
    """Superset of the frontend's TextbookImportResult TS interface.

    The full PDF pipeline (``complete_pdf_upload``) additionally returns
    ``temporary``, ``modelRuns``, ``reviewRuns`` and ``batchQuestionKeys``
    that the lightweight single-page import path does not — all optional
    here, with unknown extras still passed through.
    """

    model_config = ConfigDict(extra="allow")

    uploadId: str | None = None
    importId: str
    filename: str
    contentType: str
    size: int
    stored: bool
    temporary: bool | None = None
    modelRun: ModelRun
    modelRuns: list[ModelRun] | None = None
    ocrRun: OcrRun
    reviewRun: ReviewRun | None = None
    reviewRuns: list[ReviewRun] | None = None
    stages: list[ImportStage]
    extraction: ExtractionInfo
    batches: list[BatchInfo] | None = None
    questionPayload: QuestionPayload
    questionPayloads: list[QuestionPayload] | None = None
    batchQuestionKeys: dict[str, list[str]] | None = None


class BatchProcessResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    batch: BatchInfo
    questionPayload: QuestionPayload
    questionPayloads: list[QuestionPayload] | None = None
    ocrRun: OcrRun
    modelRun: ModelRun
    modelRuns: list[ModelRun] | None = None
    reviewRun: ReviewRun | None = None
    reviewRuns: list[ReviewRun] | None = None


class PdfUploadTask(BaseModel):
    model_config = ConfigDict(extra="allow")

    uploadId: str
    filename: str
    size: int
    chunkSize: int
    totalChunks: int
    uploadedChunks: list[int]
    status: Literal["uploading", "merging", "validating", "splitting", "ocr", "generating", "complete", "failed"]
    progress: int
    message: str
    elapsedSeconds: float
    result: TextbookImportResult | None = None


class UploadChunkAck(BaseModel):
    uploadId: str
    index: int
    received: int
    uploadedCount: int


class LibraryItem(BaseModel):
    uploadId: str
    importId: str
    filename: str
    size: int
    status: Literal["complete"]
    questionCount: int
    pageCount: int | None = None
    chapter: str
    updatedAt: float


class LibraryListResponse(BaseModel):
    items: list[LibraryItem]


class DeleteLibraryResponse(BaseModel):
    status: str
    uploadId: str


class ModelProviderInfo(BaseModel):
    id: str
    label: str
    available: bool
    models: list[str]
    detail: str


class ModelCatalog(BaseModel):
    selected: dict[str, str]
    providers: list[ModelProviderInfo]


class OcrProviderInfo(BaseModel):
    id: str
    label: str
    available: bool
    detail: str


class OcrCatalog(BaseModel):
    selected: str
    effective: str
    providers: list[OcrProviderInfo]


class HealthResponse(BaseModel):
    status: str
    database: str


class TtsStatusResponse(BaseModel):
    provider: str
    available: bool
    voice: str | None = None
    detail: str


class LessonBlockResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    type: str
    title: str = ""
    payload: dict[str, Any] = {}


class LessonDocumentResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    lessonId: str
    title: str
    version: int
    status: Literal["draft", "review", "published", "archived"]
    sourceUploadId: str | None = None
    knowledgePoints: list[str] = []
    blocks: list[LessonBlockResponse] = []
    createdAt: float | None = None
    updatedAt: float | None = None


class LearningSession(BaseModel):
    sessionId: str
    learnerId: str
    lessonId: str
    startedAt: float


class MasteryState(BaseModel):
    learnerId: str
    knowledgePoint: str
    score: float
    attemptCount: int
    correctCount: int
    lastPracticedAt: float


class MasteryListResponse(BaseModel):
    learnerId: str
    items: list[MasteryState]


class ExerciseAttemptResult(BaseModel):
    attemptId: str
    mastery: MasteryState
