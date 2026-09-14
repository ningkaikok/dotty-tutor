"""Adapter that turns one solution photo into an auditable observation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from domain.contracts.tutoring import TutorEvidenceRegion, TutorObservation
from domain.tutoring.observations import normalize_evidence_regions
from infrastructure.runtime.contracts import (
    RuntimeConfigSnapshot,
    attach_runtime_config,
)

OBSERVATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "facts": {"type": "array", "items": {"type": "string", "maxLength": 240}, "maxItems": 32},
        "recognizedText": {"type": "string", "maxLength": 4000},
        "formulaCandidates": {"type": "array", "items": {"type": "string", "maxLength": 240}, "maxItems": 32},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidenceRegions": {
            "type": "array",
            "maxItems": 64,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {key: {"type": "number", "minimum": 0, "maximum": 1} for key in ("x", "y", "width", "height")},
                "required": ["x", "y", "width", "height"],
            },
        },
    },
    "required": ["facts", "recognizedText", "formulaCandidates", "confidence", "evidenceRegions"],
}


class TutorObservationAdapter:
    """Call the existing model runtime without granting it grading authority."""

    adapter = "tutor-observation-adapter"
    version = "v1"

    def __init__(self, *, runtime: Any) -> None:
        self.runtime = runtime

    def observe(self, image_path: Path, *, question_hint: str = "") -> TutorObservation:
        selection = getattr(self.runtime, "selection", None)
        provider = str(getattr(selection, "provider", "mock"))
        model = str(getattr(selection, "model", "unknown"))
        prompt = (
            "读取学生解题步骤照片，只提取可见文字、公式候选和每条观察对应的相对证据框。"
            "不要判定答案正误，不要推断掌握度；看不清时降低 confidence 并返回空框。"
            f"\n题目提示：{question_hint[:500]}"
        )
        runtime_ref: dict[str, Any] = {
            "provider": provider,
            "model": model,
            "runtime": "tutor-observation",
            "adapter": self.adapter,
            "adapterVersion": self.version,
        }
        if provider == "mock":
            return TutorObservation(
                adapter=self.adapter,
                adapterVersion=self.version,
                runtimeRef=runtime_ref,
                confidence=0.0,
                fallback=True,
            )
        try:
            if hasattr(self.runtime, "generate_json_as"):
                generated, run = self.runtime.generate_json_as(
                    provider, model, prompt, OBSERVATION_SCHEMA, max_tokens=700, image_paths=[image_path]
                )
            else:
                generated, run = self.runtime.generate_json(prompt, OBSERVATION_SCHEMA, max_tokens=700, image_paths=[image_path])
            runtime_ref.update({key: run.get(key) for key in ("provider", "model", "runtimeConfig", "config") if isinstance(run, dict) and key in run})
            if isinstance(run, dict):
                attach_runtime_config(
                    run,
                    RuntimeConfigSnapshot.for_model(provider, model, schema=OBSERVATION_SCHEMA, prompt=prompt, runtime="tutor-observation"),
                )
            return TutorObservation(
                facts=[str(item)[:240] for item in generated.get("facts", []) if str(item).strip()][:32],
                recognizedText=str(generated.get("recognizedText") or "")[:4000],
                formulaCandidates=[str(item)[:240] for item in generated.get("formulaCandidates", []) if str(item).strip()][:32],
                confidence=max(0.0, min(1.0, float(generated.get("confidence", 0.0)))),
                evidenceRegions=[
                    TutorEvidenceRegion.model_validate(region)
                    for region in normalize_evidence_regions(generated.get("evidenceRegions"))
                ],
                adapter=self.adapter,
                adapterVersion=self.version,
                runtimeRef=runtime_ref,
                fallback=False,
            )
        except Exception as error:  # noqa: BLE001
            runtime_ref["errorType"] = type(error).__name__
            return TutorObservation(
                adapter=self.adapter,
                adapterVersion=self.version,
                runtimeRef=runtime_ref,
                confidence=0.0,
                fallback=True,
            )


__all__ = ["OBSERVATION_SCHEMA", "TutorObservationAdapter"]
