"""Validated API contracts for stateful one-question tutoring threads."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

TutorStage = Literal["diagnose", "explain", "practice", "verify"]
TutorInputMode = Literal["text", "structured"]
TutorInputStatus = Literal["received", "needs_confirmation", "confirmed", "rejected"]
TutorObservationDecisionType = Literal["confirm", "correct", "reject"]
TutorArtifactKind = Literal[
    "question-image",
    "solution-photo",
    "canvas-snapshot",
    "formula-crop",
    "text",
    "structured-answer",
]


class TutorEvidenceRegion(BaseModel):
    """Normalized evidence box shared by OCR, vision and formula adapters."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class TutorArtifactRef(BaseModel):
    """A reference to immutable input evidence; binary bytes never enter JSON/SQL."""

    artifactId: str
    inputId: str
    threadId: str
    learnerId: str
    kind: TutorArtifactKind
    mediaType: str
    filename: str
    byteSize: int = Field(ge=0)
    sha256: str
    # Storage paths are server-only and are intentionally omitted from student responses.
    storagePath: str | None = None
    createdAt: float


class TutorObservation(BaseModel):
    """Model observation, explicitly separate from deterministic grading facts."""

    facts: list[str] = Field(default_factory=list, max_length=32)
    recognizedText: str = Field(default="", max_length=4_000)
    formulaCandidates: list[str] = Field(default_factory=list, max_length=32)
    confidence: float = Field(ge=0, le=1)
    evidenceRegions: list[TutorEvidenceRegion] = Field(default_factory=list, max_length=64)
    adapter: str = "tutor-observation-adapter"
    adapterVersion: str = "v1"
    runtimeRef: dict[str, Any] = Field(default_factory=dict)
    fallback: bool = False


class TutorFormulaRecognition(BaseModel):
    """An untrusted formula candidate that needs learner confirmation before grading."""

    recognitionId: str
    latex: str = Field(default="", max_length=1_000)
    normalized: str = Field(default="", max_length=1_000)
    confidence: float = Field(ge=0, le=1)
    evidenceRegions: list[TutorEvidenceRegion] = Field(default_factory=list, max_length=64)
    sourceArtifactId: str | None = None
    authority: Literal["model", "learner-confirmed"] = "model"


class TutorCanvasState(BaseModel):
    """Replayable structured canvas state; raster snapshots remain evidence only."""

    schemaVersion: str = "math-canvas-v1"
    kind: Literal["point-placement"]
    xMin: float = -10
    xMax: float = 10
    yMin: float = -10
    yMax: float = 10
    points: list[dict[str, float | str]] = Field(default_factory=list, max_length=32)
    operations: list[dict[str, Any]] = Field(default_factory=list, max_length=200)


class TutorObservationDecision(BaseModel):
    """Append-only learner decision; it never mutates the original observation."""

    decision: TutorObservationDecisionType
    correctedText: str = Field(default="", max_length=4_000)
    correctedFacts: list[str] = Field(default_factory=list, max_length=32)
    correctedFormulaCandidates: list[str] = Field(default_factory=list, max_length=32)


class TutorInput(BaseModel):
    """Versioned multimodal input envelope shared by old and new tutor clients."""

    inputId: str
    schemaVersion: str = "tutor-input-v1"
    threadId: str
    mistakeId: str
    learnerId: str
    mode: TutorInputMode
    content: str = Field(default="", max_length=2_000)
    text: str = Field(default="", max_length=2_000)
    interactionResult: dict[str, Any] = Field(default_factory=dict)
    structuredAnswer: dict[str, Any] = Field(default_factory=dict)
    status: TutorInputStatus
    observation: TutorObservation | None = None
    artifacts: list[TutorArtifactRef] = Field(default_factory=list)
    formulaRecognitions: list[TutorFormulaRecognition] = Field(default_factory=list, max_length=32)
    canvasState: TutorCanvasState | None = None
    createdAt: float
    updatedAt: float


class TutorMessageRequest(BaseModel):
    content: str = Field(default="", max_length=2_000)
    mode: Literal["answer", "help"] = "help"
    hintLevel: int = Field(default=0, ge=0, le=3)
    interactionResult: dict[str, Any] = Field(default_factory=dict)
    inputId: str | None = Field(default=None, max_length=64)
    formulaRecognitions: list[TutorFormulaRecognition] = Field(default_factory=list, max_length=32)
    canvasState: TutorCanvasState | None = None
