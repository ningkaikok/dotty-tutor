"""Application service for multimodal TutorInput lifecycle."""

from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path
from typing import Any

from domain.contracts.tutoring import TutorFormulaRecognition, TutorObservation
from domain.tutoring.canvas import normalize_point_placement
from domain.tutoring.observations import gate_observation


class TutorInputService:
    """Keep file storage, observation gating and append-only persistence together."""

    def __init__(self, *, store: Any, observation_adapter: Any) -> None:
        self.store = store
        self.observation_adapter = observation_adapter

    def create(
        self,
        *,
        thread: dict[str, Any],
        mistake_id: str,
        learner_id: str,
        mode: str,
        content: str,
        interaction_result: dict[str, Any],
        photo_path: Path | None = None,
        artifact_kind: str = "solution-photo",
        photo_filename: str = "solution.jpg",
        photo_media_type: str = "image/jpeg",
        formula_recognitions: list[dict[str, Any]] | None = None,
        canvas_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        observation: TutorObservation | None = None
        normalized_formula_recognitions: list[dict[str, Any]] = []
        for item in formula_recognitions or []:
            normalized_formula_recognitions.append(TutorFormulaRecognition.model_validate(item).model_dump())
        normalized_canvas_state = None
        if canvas_state is not None:
            normalized_canvas_state = normalize_point_placement(canvas_state)
            if normalized_canvas_state is None:
                raise ValueError("canvasState 不是有效的 math-canvas-v1 点放置状态")
        status = "confirmed"
        artifact: dict[str, Any] | None = None
        if photo_path is not None:
            if artifact_kind not in {"question-image", "solution-photo"}:
                raise ValueError("不支持的 TutorInput 图片类型")
            observed = self.observation_adapter.observe(
                photo_path,
                question_hint=str(thread.get("mistakeId", "")),
            )
            if not isinstance(observed, TutorObservation):
                raise TypeError("Tutor observation adapter returned an invalid observation")
            observation = observed
            status = str(gate_observation(observation)["status"])
            artifact = {
                "artifact_id": uuid.uuid4().hex,
                "thread_id": thread["threadId"],
                "learner_id": learner_id,
                "kind": artifact_kind,
                "media_type": photo_media_type,
                "filename": photo_filename,
                "byte_size": photo_path.stat().st_size,
                "sha256": hashlib.sha256(photo_path.read_bytes()).hexdigest(),
                "storage_path": str(photo_path),
                "created_at": time.time(),
            }
        if normalized_formula_recognitions:
            status = "needs_confirmation"
        return self.store.create_input(
            thread_id=thread["threadId"], mistake_id=mistake_id, learner_id=learner_id, mode=mode,
            content=content, interaction_result=interaction_result, status=status,
            observation=observation.model_dump() if observation else None,
            formula_recognitions=normalized_formula_recognitions,
            canvas_state=normalized_canvas_state,
            artifact=artifact,
        )

    def decide(self, input_id: str, *, learner_id: str, decision: dict[str, Any]) -> dict[str, Any] | None:
        return self.store.append_observation_decision(
            input_id, learner_id=learner_id, decision=decision["decision"], payload=decision,
        )
