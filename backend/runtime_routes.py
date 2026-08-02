"""FastAPI routes for health, runtime selection and text-to-speech."""

from __future__ import annotations

import html
import os
import urllib.error
import urllib.request
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from model_runtime import runtime
from ocr_runtime import runtime as ocr_runtime
from question_contracts import (
    ModelSelectionRequest,
    OcrSelectionRequest,
    TtsRequest,
)


def build_runtime_router(*, store: Any, question_payload: Callable[[], dict[str, Any]]) -> APIRouter:
    router = APIRouter()
    qwen_tts_url = os.getenv("QWEN_TTS_URL", "http://127.0.0.1:8020")
    tts_provider = os.getenv("TTS_PROVIDER", "auto").lower()
    azure_speech_key = os.getenv("AZURE_SPEECH_KEY", "")
    azure_speech_region = os.getenv("AZURE_SPEECH_REGION", "")
    azure_speech_voice = os.getenv("AZURE_SPEECH_VOICE", "zh-CN-XiaoxiaoNeural")

    @router.get("/api/health")
    def health() -> dict[str, str]:
        if not store.ping():
            raise HTTPException(status_code=503, detail="数据库不可用")
        return {"status": "ok", "database": store.backend}

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
            raise HTTPException(status_code=503, detail=f"Qwen3-TTS 暂不可用：{error}") from error
        return Response(content=audio, media_type="audio/wav")

    @router.get("/api/models")
    def get_models() -> dict[str, Any]:
        return runtime.catalog()

    @router.post("/api/models/select")
    def select_model(request: ModelSelectionRequest) -> dict[str, Any]:
        try:
            return runtime.select(request.provider, request.model)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.get("/api/ocr")
    def get_ocr_providers() -> dict[str, Any]:
        return ocr_runtime.catalog()

    @router.post("/api/ocr/select")
    def select_ocr_provider(request: OcrSelectionRequest) -> dict[str, Any]:
        try:
            return ocr_runtime.select(request.provider)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.get("/api/question")
    def get_question() -> dict[str, Any]:
        return question_payload()

    return router
