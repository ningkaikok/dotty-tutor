"""HTTP contracts for importing and reviewing version-pinned quality cases."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class QualityReviewCaseImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    subject: str = Field(min_length=1, max_length=32)
    sourcePage: int = Field(ge=1, le=100_000)
    source: dict[str, Any]
    sourceLocator: dict[str, Any]
    input: dict[str, Any] = Field(default_factory=dict)
    expected: dict[str, Any]
    actual: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source")
    @classmethod
    def require_provenance(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not all(isinstance(value.get(field), str) and value[field].strip() for field in ("url", "license", "version", "contentSha256")):
            raise ValueError("source must include URL, license, version and contentSha256")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", value["contentSha256"]):
            raise ValueError("source.contentSha256 must be a SHA-256 fingerprint")
        if any(key in value for key in ("file", "path", "sourceFilePath")):
            raise ValueError("source file paths are not accepted; use a managed asset")
        if "sourceKind" in value or "counted" in value or "assetVerification" in value:
            raise ValueError("source kind, verification state and counted status are server-managed")
        if len(value["url"]) > 500 or len(value["license"]) > 256 or len(value["version"]) > 128:
            raise ValueError("source metadata exceeds limits")
        return value

    @model_validator(mode="after")
    def require_locator(self) -> QualityReviewCaseImport:
        quote = self.sourceLocator.get("quote")
        if not isinstance(quote, str) or not quote.strip() or len(quote) > 20_000:
            raise ValueError("sourceLocator.quote is required and limited to 20,000 characters")
        return self


class QualityReviewPackageImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=240)
    source: dict[str, Any] = Field(default_factory=dict)
    cases: list[QualityReviewCaseImport] = Field(min_length=1, max_length=250)


class QualityReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRecordVersion: int = Field(ge=1)
    decision: Literal["accepted", "rejected", "needs_changes"]
    rationale: str = Field(min_length=1, max_length=4000)
    verifiedQuote: str = Field(default="", max_length=20_000)
    displayName: str = Field(default="", max_length=128)


class QualityReviewRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRecordVersion: int = Field(ge=1)
    input: dict[str, Any]
    expected: dict[str, Any]
    actual: dict[str, Any]

    @model_validator(mode="after")
    def limit_snapshot_size(self) -> QualityReviewRevisionRequest:
        payload_size = len(json.dumps(
            {"input": self.input, "expected": self.expected, "actual": self.actual},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8"))
        if payload_size > 250_000:
            raise ValueError("修订快照不能超过 250 KB")
        return self


class QualityReviewPackageList(BaseModel):
    items: list[dict[str, Any]]


__all__ = [
    "QualityReviewCaseImport",
    "QualityReviewPackageImport",
    "QualityReviewDecisionRequest",
    "QualityReviewRevisionRequest",
    "QualityReviewPackageList",
]
