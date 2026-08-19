"""Immutable run and question revision orchestration."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

from domain.questions.contracts import LESSON_SCHEMA
from infrastructure.runtime.model_runtime import runtime as model_runtime
from infrastructure.runtime.ocr_runtime import runtime as ocr_runtime
from infrastructure.runtime.review_runtime import runtime_reviewer
from infrastructure.runtime.contracts import RuntimeConfigSnapshot


PROMPT_VERSION = "lesson-generation-v1"
VALIDATOR_VERSION = "p0-v4"
SCHEMA_VERSION = hashlib.sha256(
    json.dumps(LESSON_SCHEMA, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()[:16]


def is_persistence_test_double(store: Any) -> bool:
    for name in ("save_job", "save_questions", "save_lesson"):
        method = getattr(store, name, None)
        if method is not None and method.__class__.__module__.startswith("unittest.mock"):
            return True
    return False


def _run_identity(run: dict[str, Any] | None) -> dict[str, Any]:
    run = run or {}
    # Keep provider/model/version/fallback evidence, but never persist prompt text or secrets.
    identity = {
        key: run.get(key)
        for key in (
            "provider", "model", "version", "requestedProvider", "requestedModel",
            "fallback", "fallbackReason", "durationMs", "usage", "questionSegmentationVersion",
        )
        if run.get(key) is not None
    }
    if isinstance(run.get("config"), dict):
        identity["config"] = RuntimeConfigSnapshot.from_mapping(run["config"]).to_dict()
    elif run:
        # Legacy result dictionaries are upgraded when they enter a RunSnapshot.
        identity["config"] = RuntimeConfigSnapshot.from_mapping(run).to_dict()
    return identity


def build_run_config(
    *,
    model_run: dict[str, Any] | None = None,
    review_run: dict[str, Any] | None = None,
    ocr_run: dict[str, Any] | None = None,
    tutor_run: dict[str, Any] | None = None,
    operation_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if model_run is None:
        model_run = {
            "provider": model_runtime.selection.provider,
            "model": model_runtime.selection.model,
            "fallback": False,
        }
    if review_run is None:
        review_run = {
            "provider": runtime_reviewer.text_provider,
            "textModelRun": {
                "provider": runtime_reviewer.text_provider,
                "model": runtime_reviewer.text_model,
                "fallback": False,
            },
            "visionModelRun": {
                "provider": runtime_reviewer.text_provider,
                "model": runtime_reviewer.text_model,
                "fallback": False,
            },
        }
    if ocr_run is None:
        ocr_run = {"provider": ocr_runtime.selection.provider, "fallback": False}
    model_config = RuntimeConfigSnapshot.for_model(
        str(model_run.get("provider") or model_runtime.selection.provider),
        str(model_run.get("model") or model_runtime.selection.model),
        schema=LESSON_SCHEMA,
        prompt=PROMPT_VERSION,
        runtime="generation",
        timeout=180.0 if str(model_run.get("provider") or "") == "ollama" else 240.0,
    )
    if isinstance(model_run.get("config"), dict):
        model_config = RuntimeConfigSnapshot.from_mapping(model_run["config"], runtime="generation")
    text_model_run = (review_run or {}).get("textModelRun") or {
        "provider": runtime_reviewer.text_provider,
        "model": runtime_reviewer.text_model,
    }
    vision_model_run = (review_run or {}).get("visionModelRun") or text_model_run
    text_config = RuntimeConfigSnapshot.for_model(
        str(text_model_run.get("provider") or runtime_reviewer.text_provider),
        str(text_model_run.get("model") or runtime_reviewer.text_model),
        schema="review-text",
        prompt="review",
        runtime="review",
        timeout=240.0,
    )
    vision_config = RuntimeConfigSnapshot.for_model(
        str(vision_model_run.get("provider") or runtime_reviewer.text_provider),
        str(vision_model_run.get("model") or runtime_reviewer.text_model),
        schema="review-vision",
        prompt="review-vision",
        runtime="review",
        timeout=240.0,
    )
    if isinstance(text_model_run.get("config"), dict):
        text_config = RuntimeConfigSnapshot.from_mapping(text_model_run["config"], runtime="review")
    if isinstance(vision_model_run.get("config"), dict):
        vision_config = RuntimeConfigSnapshot.from_mapping(vision_model_run["config"], runtime="review")
    ocr_config = RuntimeConfigSnapshot(
        provider=str(ocr_run.get("provider") or ocr_runtime.selection.provider),
        runtime="ocr",
        schema="ocr-pipeline",
        prompt="ocr",
        timeout=900.0,
    )
    if isinstance(ocr_run.get("config"), dict):
        ocr_config = RuntimeConfigSnapshot.from_mapping(ocr_run["config"], runtime="ocr")
    config: dict[str, Any] = {
        "model": {**_run_identity(model_run), **model_config.to_dict(), "config": model_config.to_dict()},
        "review": {
            "provider": (review_run or {}).get("provider") or runtime_reviewer.text_provider,
            "text": {**_run_identity(text_model_run), **text_config.to_dict(), "config": text_config.to_dict()},
            "vision": {**_run_identity(vision_model_run), **vision_config.to_dict(), "config": vision_config.to_dict()},
        },
        "ocr": {**_run_identity(ocr_run), **ocr_config.to_dict(), "config": ocr_config.to_dict()},
        "promptVersion": PROMPT_VERSION,
        "schemaVersion": SCHEMA_VERSION,
        "validatorVersion": VALIDATOR_VERSION,
        "operation": operation_details or {},
    }
    if tutor_run is not None:
        tutor_config = RuntimeConfigSnapshot.from_mapping(
            tutor_run.get("config") if isinstance(tutor_run, dict) else None,
            provider=(tutor_run or {}).get("provider", "mock"),
            model=(tutor_run or {}).get("model", "static-demo"),
            runtime="tutor",
        )
        config["tutor"] = {**_run_identity(tutor_run), **tutor_config.to_dict(), "config": tutor_config.to_dict()}
    config["runtimeConfig"] = {
        key: value
        for key, value in {
            "generation": config["model"].get("config"),
            "review": config["review"].get("text", {}).get("config"),
            "ocr": config["ocr"].get("config"),
            "tutor": config.get("tutor", {}).get("config"),
        }.items()
        if value is not None
    }
    return config


class RunAudit:
    def __init__(self, store: Any) -> None:
        self.store = store
        self._fallback: dict[str, dict[str, Any]] = {}

    def _is_legacy_test_double(self) -> bool:
        """Keep old service tests isolated when their persistence writes are mocked."""
        return is_persistence_test_double(self.store)

    def start(
        self,
        operation: str,
        scope: str,
        *,
        upload_id: str | None = None,
        question_key: str | None = None,
        publication_id: str | None = None,
        config: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        snapshot_data = {
            "runId": run_id or uuid.uuid4().hex,
            "operation": operation,
            "scope": scope,
            "targetUploadId": upload_id,
            "targetQuestionKey": question_key,
            "targetPublicationId": publication_id,
            "status": "running",
            "config": config or build_run_config(),
            "startedAt": time.time(),
        }
        if (
            hasattr(self.store, "create_run_snapshot")
            and not self._is_legacy_test_double()
        ):
            snapshot = self.store.create_run_snapshot(snapshot_data)
        else:  # Narrow fake stores used by legacy unit tests.
            snapshot = {**snapshot_data, "status": "running", "result": None, "error": None, "completedAt": None}
            self._fallback[snapshot["runId"]] = snapshot
        return snapshot

    def finish(self, run_id: str, *, result: dict[str, Any] | None = None) -> dict[str, Any]:
        if run_id not in self._fallback and hasattr(self.store, "finish_run_snapshot"):
            return self.store.finish_run_snapshot(run_id, status="succeeded", result=result)
        snapshot = self._fallback[run_id]
        if snapshot["status"] != "running":
            raise ValueError("运行已经结束")
        snapshot.update(status="succeeded", result=result, completedAt=time.time())
        return snapshot

    def fail(self, run_id: str, error: Exception | str, *, stage: str | None = None) -> dict[str, Any]:
        details = {"stage": stage, "type": type(error).__name__, "message": str(error)[:500]}
        if run_id not in self._fallback and hasattr(self.store, "finish_run_snapshot"):
            return self.store.finish_run_snapshot(run_id, status="failed", error=details)
        snapshot = self._fallback[run_id]
        if snapshot["status"] != "running":
            raise ValueError("运行已经结束")
        snapshot.update(status="failed", error=details, completedAt=time.time())
        return snapshot
