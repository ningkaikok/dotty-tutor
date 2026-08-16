"""Validated API contracts for stateful one-question tutoring threads."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


TutorStage = Literal["diagnose", "explain", "practice", "verify"]
TutorInputMode = Literal["text", "structured"]


class TutorMessageRequest(BaseModel):
    content: str = Field(default="", max_length=2_000)
    mode: Literal["answer", "help"] = "help"
    hintLevel: int = Field(default=0, ge=0, le=3)
    interactionResult: dict[str, Any] = Field(default_factory=dict)
