"""HTTP contracts for source-versioned chapter courses."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from domain.contracts.lesson import LessonBlock


class ChapterRegion(BaseModel):
    regionId: str | None = Field(default=None, max_length=128)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    assetId: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def region_fits_page(self) -> ChapterRegion:
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("来源区域必须位于归一化页面边界内")
        return self


class ChapterSentence(BaseModel):
    sentenceId: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=20000)
    regionId: str | None = Field(default=None, max_length=128)


class ChapterPageFlag(BaseModel):
    page: int = Field(ge=1, le=100_000)
    flags: list[Literal["missing_conditions", "wrong_figure", "missing_page", "unreadable"]] = Field(default_factory=list, max_length=20)


class ChapterPage(BaseModel):
    page: int = Field(ge=1, le=100_000)
    text: str = Field(max_length=20000)
    regions: list[ChapterRegion] = Field(default_factory=list, max_length=100)
    flags: list[str] = Field(default_factory=list, max_length=20)
    sentences: list[ChapterSentence] = Field(default_factory=list, max_length=200)


class ChapterSource(BaseModel):
    uploadId: str | None = Field(default=None, max_length=64)
    sourceVersion: str = Field(default="1", min_length=1, max_length=128)
    license: str | None = Field(default=None, max_length=256)
    pageStart: int | None = Field(default=None, ge=1, le=100_000)
    pageEnd: int | None = Field(default=None, ge=1, le=100_000)
    pages: list[ChapterPage] = Field(default_factory=list, max_length=80)
    pageFlags: list[ChapterPageFlag] = Field(default_factory=list, max_length=80)


class ChapterCreate(BaseModel):
    subject: Literal["math", "english"]
    title: str = Field(min_length=1, max_length=200)
    source: ChapterSource


class ChapterRevisionCreate(BaseModel):
    source: ChapterSource
    expectedRecordVersion: int = Field(ge=1)


class ChapterReview(BaseModel):
    decision: Literal["approve", "request_changes"]
    reviewer: str = Field(min_length=1, max_length=128)
    note: str = Field(default="", max_length=1000)
    expectedRecordVersion: int = Field(ge=1)


class ChapterAttemptReview(BaseModel):
    reviewer: str = Field(min_length=1, max_length=128)
    decision: Literal["correct", "incorrect"]
    note: str = Field(default="", max_length=1000)


class ChapterLessonEdit(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    answer: str = Field(min_length=1, max_length=1000)
    answerType: Literal["text", "numeric"] = "text"
    questionKind: Literal["word_meaning", "reference", "explicit", "inference", "short_answer"] = "short_answer"
    answerMode: Literal["objective", "short_answer"] = "short_answer"
    acceptedAnswers: list[str] = Field(default_factory=list, max_length=20)
    requiredEvidenceRefs: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    rubric: dict[str, Any] = Field(default_factory=dict)
    conceptMarkdown: str | None = Field(default=None, max_length=12000)
    exampleText: str | None = Field(default=None, max_length=12000)
    hint: str | None = Field(default=None, max_length=2000)
    sourceRevisionId: str = Field(min_length=1, max_length=64)
    page: int = Field(ge=1)
    expectedRecordVersion: int = Field(ge=1)


class ChapterAttempt(BaseModel):
    attemptId: str = Field(min_length=1, max_length=64)
    lessonId: str = Field(min_length=1, max_length=128)
    questionId: str = Field(min_length=1, max_length=128)
    learnerId: str | None = Field(default=None, min_length=1, max_length=128)
    publicationId: str | None = Field(default=None, max_length=64)
    answer: dict[str, Any] = Field(default_factory=dict)
    evidenceRefs: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class ChapterResponse(BaseModel):
    chapterId: str
    subject: Literal["math", "english"]
    title: str
    status: str
    version: int
    recordVersion: int
    sourceRevisions: list[ChapterSourceRevisionView]
    lessons: list[ChapterLessonView]
    currentLessonIds: list[str]
    reviewIssues: list[ChapterIssue]
    publicationId: str | None = None
    publications: list[ChapterPublicationView]


class ChapterPublishResponse(BaseModel):
    chapterId: str
    publicationId: str
    version: int
    status: Literal["published"]


class ChapterAttemptResponse(BaseModel):
    attemptId: str
    publicationId: str
    lessonId: str
    answer: dict[str, Any] = Field(default_factory=dict)
    evidenceRefs: list[dict[str, Any]] = Field(default_factory=list)
    learnerId: str | None = None
    assessment: Literal["correct", "incorrect", "needs_review"]
    evidenceVerdict: Literal["supported", "mismatch", "missing", "needs_review"]
    feedback: dict[str, Any]
    mastery: dict[str, Any] | None = None
    reviews: list[dict[str, Any]] = Field(default_factory=list)


class ChapterPublishedResponse(BaseModel):
    chapterId: str
    subject: Literal["math", "english"]
    title: str
    publicationId: str
    version: int
    status: Literal["published"]
    lessons: list[ChapterPublicLessonView]


class ChapterListResponse(BaseModel):
    items: list[ChapterSummaryView]


class ChapterIssue(BaseModel):
    code: str
    message: str
    lessonId: str | None = None


class ChapterReviewRecord(BaseModel):
    reviewer: str
    decision: Literal["approve", "request_changes", "correct", "incorrect"]
    note: str = ""
    createdAt: float | None = None


class ChapterSourceRevisionView(BaseModel):
    sourceRevisionId: str
    fingerprint: str
    uploadId: str | None = None
    sourceVersion: str
    license: str | None = None
    pageStart: int
    pageEnd: int
    pages: list[ChapterPage]
    issues: list[ChapterIssue]
    createdAt: float


class ChapterLessonView(BaseModel):
    lessonId: str
    title: str
    version: int
    status: str
    sourceRevisionId: str | None = None
    sourceLocator: ChapterSourceLocator | None = None
    reviewIssues: list[ChapterIssue] = Field(default_factory=list)
    reviews: list[ChapterReviewRecord] = Field(default_factory=list)
    knowledgePoints: list[str] = Field(default_factory=list)
    evidenceOptions: list[ChapterEvidenceOption] = Field(default_factory=list)
    blocks: list[LessonBlock]
    questionPayload: ChapterAuthorQuestionPayload


class ChapterPublicationView(BaseModel):
    publicationId: str
    version: int
    sourceRevisionId: str
    publishedAt: float


class ChapterSummaryView(BaseModel):
    chapterId: str
    subject: Literal["math", "english"]
    title: str
    status: str
    version: int
    recordVersion: int
    currentLessonIds: list[str]
    reviewIssues: list[ChapterIssue]
    publicationId: str | None = None


class ChapterEvidenceRef(BaseModel):
    sourceRevisionId: str
    page: int = Field(ge=1, le=100_000)
    regionId: str | None = None
    sentenceId: str | None = None
    quote: str | None = None


class ChapterEvidenceOption(ChapterEvidenceRef):
    label: str = Field(max_length=20000)


class ChapterSourceLocator(BaseModel):
    sourceRevisionId: str
    page: int = Field(ge=1, le=100_000)
    regions: list[ChapterRegion] = Field(default_factory=list)


class ChapterAuthorQuestion(BaseModel):
    id: str
    questionType: str
    questionKind: str | None = None
    answerMode: str | None = None
    prompt: str
    knowledgePoint: str | None = None
    correctAnswers: list[str] | None = None
    answerSpec: dict[str, Any] | None = None
    acceptedAnswers: list[str] | None = None
    requiredEvidenceRefs: list[ChapterEvidenceRef] = Field(default_factory=list)
    rubric: dict[str, Any] | None = None
    evaluation: dict[str, Any] | None = None
    subject: Literal["math", "english"] | None = None
    sourceRevisionId: str | None = None
    sourceLocator: ChapterSourceLocator | None = None
    options: list[dict[str, Any]] | None = None


class ChapterAuthorQuestionPayload(BaseModel):
    question: ChapterAuthorQuestion
    lessonSteps: list[dict[str, Any]] = Field(default_factory=list)
    quality: dict[str, Any] = Field(default_factory=dict)


class ChapterPublicQuestion(BaseModel):
    id: str
    questionType: str
    questionKind: str | None = None
    prompt: str
    knowledgePoint: str | None = None
    sourceRevisionId: str | None = None
    sourceLocator: ChapterSourceLocator | None = None
    options: list[dict[str, Any]] | None = None


class ChapterPublicQuestionPayload(BaseModel):
    question: ChapterPublicQuestion
    lessonSteps: list[dict[str, Any]] = Field(default_factory=list)


class ChapterPublicLessonView(BaseModel):
    lessonId: str
    title: str
    version: int
    status: str
    knowledgePoints: list[str]
    blocks: list[LessonBlock]
    questionPayload: ChapterPublicQuestionPayload
    sourceRevisionId: str | None = None
    sourceLocator: ChapterSourceLocator | None = None
    evidenceOptions: list[ChapterEvidenceOption] = Field(default_factory=list)
