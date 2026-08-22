"""Validated contracts for adaptive variation practice."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class VariationAnswerRequest(BaseModel):
    content: str = Field(default="", max_length=2_000)
    interactionResult: dict[str, Any] = Field(default_factory=dict)
