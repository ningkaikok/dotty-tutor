"""HTTP contracts for recoverable, non-assessment voice tutoring sessions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class VoiceSessionCreate(BaseModel):
    learnerId: str = Field(min_length=1, max_length=128)
    chapterId: str = Field(min_length=1, max_length=128)
    publicationId: str = Field(min_length=1, max_length=128)
    lessonId: str = Field(min_length=1, max_length=128)
    blockId: str = Field(min_length=1, max_length=128)


class VoiceTranscriptDraft(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    source: Literal["manual", "browser_speech_recognition", "speech_to_text"] = "manual"
    browserConfidence: float | None = Field(default=None, ge=0, le=1)


class VoiceTranscriptConfirm(BaseModel):
    revision: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=5000)


class VoiceResponseRequest(BaseModel):
    turnId: str = Field(min_length=1, max_length=128)
    transcriptRevision: int = Field(ge=1)
    expectedGeneration: int = Field(ge=0)


class VoiceCancelRequest(BaseModel):
    expectedGeneration: int = Field(ge=0)


class VoiceResponse(BaseModel):
    status: Literal["completed", "superseded", "duplicate"]
    sessionId: str
    generation: int
    turn: dict[str, Any] | None = None


class VoiceTranscriptResult(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    confidence: dict | None = None
    ambiguityFlags: list[str] = Field(default_factory=list)
    needsConfirmation: bool = True
