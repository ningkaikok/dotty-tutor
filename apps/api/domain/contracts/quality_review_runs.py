"""HTTP contracts for bounded, anonymous quality-review model runs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class QualityReviewModelSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=128)


class QualityReviewRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caseIds: list[str] = Field(min_length=1, max_length=6)
    taskInstruction: str = Field(min_length=1, max_length=4000)
    baseline: QualityReviewModelSelection
    candidate: QualityReviewModelSelection
    perCallTimeoutSeconds: int = Field(default=30, ge=1, le=60)
    totalBudgetSeconds: int = Field(default=240, ge=1, le=300)
    maxOutputTokens: int = Field(default=500, ge=100, le=700)

    @field_validator("caseIds")
    @classmethod
    def unique_case_ids(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item or len(item) > 64 for item in cleaned):
            raise ValueError("caseIds must contain non-empty IDs up to 64 characters")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("caseIds must be unique")
        return cleaned

    @field_validator("taskInstruction")
    @classmethod
    def non_empty_instruction(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("taskInstruction cannot be blank")
        return cleaned


class QualityReviewRunRating(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caseId: str = Field(min_length=1, max_length=64)
    aPass: bool
    bPass: bool
    preferred: Literal["A", "B", "tie", "neither"]
    rationale: str = Field(min_length=1, max_length=2000)

    @field_validator("rationale")
    @classmethod
    def non_empty_rationale(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("rationale cannot be blank")
        return cleaned


__all__ = [
    "QualityReviewModelSelection",
    "QualityReviewRunCreate",
    "QualityReviewRunRating",
]
