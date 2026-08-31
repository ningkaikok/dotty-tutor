"""FastAPI routes for health, runtime selection and text-to-speech."""

from __future__ import annotations

import html
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from application.errors import AppError
from application.services.learning_funnel import build_funnel_snapshot
from domain.constants import DEMO_LEARNER_ID
from domain.questions.contracts import (
    ModelSelectionRequest,
    OcrSelectionRequest,
    TtsRequest,
)
from infrastructure.runtime.model_runtime import runtime
from infrastructure.runtime.ocr_runtime import runtime as ocr_runtime
from infrastructure.runtime.review_runtime import runtime_reviewer
from observability import log_event


def build_runtime_router(
    *,
    store: Any,
    question_payload: Callable[[], dict[str, Any]],
    tutor_runtime: Any,
    metrics_store: Any = None,
) -> APIRouter:
    router = APIRouter()
    qwen_tts_url = os.getenv("QWEN_TTS_URL", "http://127.0.0.1:8020")
    tts_provider = os.getenv("TTS_PROVIDER", "auto").lower()
    azure_speech_key = os.getenv("AZURE_SPEECH_KEY", "")
    azure_speech_region = os.getenv("AZURE_SPEECH_REGION", "")
    azure_speech_voice = os.getenv("AZURE_SPEECH_VOICE", "zh-CN-XiaoxiaoNeural")

    @router.get("/api/health")
    def health() -> dict[str, str]:
        try:
            schema = store.schema_status()
        except Exception as error:
            log_event("service.health.failed", level=40, dependency="database")
            raise AppError(
                "数据库不可用",
                status_code=503,
                error_code="DATABASE_UNAVAILABLE",
                retryable=True,
            ) from error
        if not schema["ready"]:
            log_event(
                "service.health.failed",
                level=40,
                dependency="database-schema",
                schema_version=schema.get("version"),
                missing_tables=len(schema.get("missingTables", [])),
                missing_columns=len(schema.get("missingColumns", {})),
            )
            raise AppError(
                "数据库 schema 未就绪，请先执行迁移命令",
                status_code=503,
                error_code="SCHEMA_OUT_OF_DATE",
                retryable=False,
                details={
                    "version": schema.get("version"),
                    "head": schema.get("head"),
                    "versionState": schema.get("versionState"),
                    "missingTables": schema.get("missingTables", []),
                    "missingColumns": schema.get("missingColumns", {}),
                    "missingIndexes": schema.get("missingIndexes", []),
                    "missingForeignKeys": schema.get("missingForeignKeys", []),
                    "orphanCounts": schema.get("orphanCounts", {}),
                },
            )
        log_event("service.health.ok", level=10, database=store.backend)
        return {"status": "ok", "database": store.backend, "schema": "current"}

    @router.get("/api/tts/status")
    def tts_status() -> dict[str, Any]:
        if tts_provider in {"auto", "azure"} and azure_speech_key and azure_speech_region:
            return {
                "provider": "azure-speech-neural",
                "available": True,
                "voice": azure_speech_voice,
                "detail": "Azure Speech Neural 已配置",
            }
        if tts_provider == "azure":
            return {"provider": "browser", "available": False, "detail": "缺少 AZURE_SPEECH_KEY 或 AZURE_SPEECH_REGION"}
        try:
            with urllib.request.urlopen(f"{qwen_tts_url}/health", timeout=1) as response:
                data = response.read().decode("utf-8")
            return {"provider": "qwen3-tts", "available": True, "detail": data}
        except (OSError, urllib.error.URLError):
            log_event("tts.health.failed", level=30, provider="qwen3-tts")
            return {"provider": "browser", "available": False, "detail": "Qwen3-TTS 服务未启动，前端将回退到浏览器语音"}

    def synthesize_azure_tts(request: TtsRequest) -> Response:
        ssml = (
            '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="zh-CN">'
            f'<voice name="{html.escape(azure_speech_voice, quote=True)}">'
            f'<prosody rate="-5%">{html.escape(request.text)}</prosody>'
            "</voice></speak>"
        ).encode("utf-8")
        azure_request = urllib.request.Request(
            f"https://{azure_speech_region}.tts.speech.microsoft.com/cognitiveservices/v1",
            data=ssml,
            headers={
                "Ocp-Apim-Subscription-Key": azure_speech_key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                "User-Agent": "dotty-tutor",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(azure_request, timeout=30) as response:
                audio = response.read()
        except (OSError, urllib.error.URLError) as error:
            raise HTTPException(status_code=503, detail=f"Azure Speech 调用失败：{error}") from error
        return Response(content=audio, media_type="audio/mpeg")

    @router.post("/api/tts")
    def synthesize_tts(request: TtsRequest) -> Response:
        if tts_provider in {"auto", "azure"} and azure_speech_key and azure_speech_region:
            return synthesize_azure_tts(request)
        if tts_provider == "azure":
            raise HTTPException(status_code=503, detail="Azure Speech 未配置，请设置 AZURE_SPEECH_KEY 和 AZURE_SPEECH_REGION")
        import json

        body = json.dumps({"text": request.text, "speaker": request.speaker, "instruct": request.instruct}, ensure_ascii=False).encode("utf-8")
        proxy_request = urllib.request.Request(
            f"{qwen_tts_url}/tts",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(proxy_request, timeout=180) as response:
                audio = response.read()
        except (OSError, urllib.error.URLError) as error:
            log_event(
                "tts.request.failed",
                level=40,
                provider="qwen3-tts",
                text_length=len(request.text),
                error_type=type(error).__name__,
                error=str(error)[:300],
                exc_info=True,
            )
            raise HTTPException(status_code=503, detail=f"Qwen3-TTS 暂不可用：{error}") from error
        log_event("tts.request.completed", provider="qwen3-tts", text_length=len(request.text), audio_bytes=len(audio))
        return Response(content=audio, media_type="audio/wav")

    @router.get("/api/models")
    def get_models() -> dict[str, Any]:
        return runtime.catalog()

    @router.post("/api/models/select")
    def select_model(request: ModelSelectionRequest) -> dict[str, Any]:
        try:
            result = runtime.select(request.provider, request.model)
            log_event("model.selection.changed", provider=request.provider, model=request.model)
            return result
        except ValueError as error:
            log_event("model.selection.failed", level=30, provider=request.provider, model=request.model, error=str(error)[:200])
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.get("/api/metrics/model-calls")
    def get_model_call_metrics(days: int = 7) -> dict[str, Any]:
        """模型调用边界指标聚合（只读；按 runtime/task/provider/model 分组）。"""
        if metrics_store is None:
            raise HTTPException(status_code=503, detail="指标存储未注入")
        window = max(1, min(days, 90))
        return {"days": window, "items": metrics_store.aggregate(days=window)}

    @router.get("/api/reports/learning-cost")
    def get_learning_cost_report(
        learnerId: str = DEMO_LEARNER_ID,
        days: int = 7,
    ) -> dict[str, Any]:
        """Return learning outcomes alongside global model proxy metrics.

        Learning is a cumulative learner snapshot; model calls are a global
        rolling window and are intentionally not attributed to that learner.
        """
        if metrics_store is None:
            raise HTTPException(status_code=503, detail="指标存储未注入")
        window = max(1, min(days, 90))
        return {
            "learnerId": learnerId,
            "days": window,
            "generatedAt": time.time(),
            "scope": {
                "learning": "learner_cumulative",
                "modelCalls": "global_rolling_window",
                "costUnit": "proxy_only",
            },
            "learning": build_funnel_snapshot(store.engine, learnerId),
            "modelCost": metrics_store.aggregate_report(days=window),
            "limitations": [
                "调用数是逻辑 Runtime 调用，不等于 Provider 实际重试次数",
                "模型指标是全局窗口数据，不提供学生级成本归因",
                "成本仅表示调用、耗时和 Token 代理指标，不表示货币成本或学习效果因果关系",
            ],
        }

    @router.get("/api/tutor-models")
    def get_tutor_models() -> dict[str, Any]:
        """Return the independent model catalog used only by mistake tutoring."""
        return tutor_runtime.catalog()

    @router.post("/api/tutor-models/select")
    def select_tutor_model(request: ModelSelectionRequest) -> dict[str, Any]:
        """Switch future tutoring turns without changing generation/review models."""
        try:
            result = tutor_runtime.select(request.provider, request.model)
            log_event("tutor.model.selection.changed", provider=request.provider, model=request.model)
            return result
        except ValueError as error:
            log_event(
                "tutor.model.selection.failed",
                level=30,
                provider=request.provider,
                model=request.model,
                error=str(error)[:200],
            )
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.get("/api/review-models")
    def get_review_models() -> dict[str, Any]:
        return runtime_reviewer.catalog()

    @router.post("/api/review-models/select")
    def select_review_model(request: ModelSelectionRequest) -> dict[str, Any]:
        try:
            result = runtime_reviewer.select_text(request.provider, request.model)
            log_event(
                "review.model.selection.changed",
                provider=request.provider,
                model=request.model,
            )
            return result
        except ValueError as error:
            log_event(
                "review.model.selection.failed",
                level=30,
                provider=request.provider,
                model=request.model,
                error=str(error)[:200],
            )
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.get("/api/ocr")
    def get_ocr_providers() -> dict[str, Any]:
        return ocr_runtime.catalog()

    @router.post("/api/ocr/select")
    def select_ocr_provider(request: OcrSelectionRequest) -> dict[str, Any]:
        try:
            result = ocr_runtime.select(request.provider)
            log_event("ocr.selection.changed", provider=request.provider)
            return result
        except ValueError as error:
            log_event("ocr.selection.failed", level=30, provider=request.provider, error=str(error)[:200])
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.get("/api/question")
    def get_question() -> dict[str, Any]:
        return question_payload()

    return router
