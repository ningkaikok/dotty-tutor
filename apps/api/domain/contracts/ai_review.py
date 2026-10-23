"""Contracts for AI pre-review of courseware pages and source cases.

The model only reports findings. Routing, sampling and automatic completion stay
with deterministic code; AI verdicts never count as human gold labels.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AiVerdict = Literal["pass", "concern", "fail"]
AiIssueCode = Literal[
    "excerpt_mismatch", "formula_error", "figure_mismatch", "answer_error",
    "unsupported_content", "missing_condition", "other",
]
AiRoute = Literal["not_reviewed", "stale", "needs_review", "bulk_confirmable", "decided"]


class AiReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: AiIssueCode
    severity: Literal["blocking", "minor"]
    message: str = Field(min_length=1, max_length=500)
    sourcePage: int | None = Field(default=None, ge=1, le=100_000)
    quote: str = Field(default="", max_length=300)


class AiReviewModelOutput(BaseModel):
    """Strict schema the reviewer model must return for one page or case."""

    model_config = ConfigDict(extra="forbid")

    verdict: AiVerdict
    summary: str = Field(min_length=1, max_length=500)
    issues: list[AiReviewIssue] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def verdict_matches_issues(self) -> AiReviewModelOutput:
        # A self-contradictory answer is rejected rather than routed optimistically.
        blocking = any(issue.severity == "blocking" for issue in self.issues)
        if self.verdict == "pass" and blocking:
            raise ValueError("通过结论不能包含阻断问题")
        if self.verdict == "fail" and not blocking:
            raise ValueError("不通过结论必须至少列出一个阻断问题")
        return self


class AiReviewModel(BaseModel):
    provider: str
    model: str


class AiPageReview(BaseModel):
    """One page's AI verdict and routing next to the teacher's current decision."""

    pageId: str
    title: str
    pageVersion: int
    reviewStatus: Literal["pending", "approved", "changes_requested"]
    route: AiRoute
    sampled: bool
    verdict: AiVerdict | None = None
    summary: str = ""
    issues: list[AiReviewIssue] = Field(default_factory=list)
    reviewedVersion: int | None = None
    textOnly: bool = False
    model: AiReviewModel | None = None
    reviewedAt: float | None = None


class AiAgreementMetrics(BaseModel):
    """Teacher decisions recorded after an AI verdict on the same page version."""

    compared: int = 0
    agreements: int = 0
    # AI passed, teacher requested changes: the number that gates any future auto-release.
    missed: int = 0
    # AI raised a concern or failed the page, teacher approved it unchanged.
    falseAlarms: int = 0


class CoursewareAiReviewStatus(BaseModel):
    coursewareId: str
    recordVersion: int
    samplingRate: float
    pagesToReview: int
    pages: list[AiPageReview]
    metrics: AiAgreementMetrics
    jobId: str | None = None
    countsAsHumanReview: Literal[False] = False


class CoursewareAiReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRecordVersion: int = Field(ge=1)


class CoursewareAiReviewStart(BaseModel):
    """Estimate first: pagesToReview equals the model calls this request will make."""

    coursewareId: str
    pagesToReview: int
    cachedPages: int
    jobId: str | None = None
    reviewer: AiReviewModel


class SourceCaseAiOpinion(BaseModel):
    """Advisory opinion on a source case; never a human decision or gold label."""

    caseId: str
    caseVersion: int
    verdict: AiVerdict | None = None
    summary: str = ""
    issues: list[AiReviewIssue] = Field(default_factory=list)
    stale: bool = False
    textOnly: bool = False
    model: AiReviewModel | None = None
    reviewedAt: float | None = None
    jobId: str | None = None
    countsAsHumanReview: Literal[False] = False
