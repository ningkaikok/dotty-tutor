"""Deterministic gate for untrusted multimodal observations."""

from __future__ import annotations

from typing import Any

from domain.contracts.tutoring import TutorObservation

OBSERVATION_GATE_THRESHOLD = 0.80


def normalize_evidence_regions(value: Any) -> list[dict[str, float]]:
    """Normalize provider boxes to relative x/y/width/height values."""
    if not isinstance(value, list):
        return []
    result: list[dict[str, float]] = []
    for region in value[:64]:
        if not isinstance(region, dict):
            continue
        try:
            values = {key: max(0.0, min(1.0, float(region.get(key, 0)))) for key in ("x", "y", "width", "height")}
        except (TypeError, ValueError):
            continue
        if values["width"] > 0 and values["height"] > 0:
            result.append(values)
    return result


def observation_status(observation: TutorObservation, *, threshold: float = OBSERVATION_GATE_THRESHOLD) -> str:
    """Return a gate status; observations never become grading facts by themselves."""
    if observation.fallback or observation.confidence < threshold or not observation.evidenceRegions:
        return "needs_confirmation"
    return "confirmed"


def gate_observation(observation: TutorObservation, *, threshold: float = OBSERVATION_GATE_THRESHOLD) -> dict[str, Any]:
    status = observation_status(observation, threshold=threshold)
    return {
        "status": status,
        "allowedForDeterministicGrading": False,
        "requiresStudentConfirmation": status != "confirmed",
        "threshold": threshold,
    }
