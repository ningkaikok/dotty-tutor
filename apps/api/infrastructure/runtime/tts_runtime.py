"""Bounded adapter for the application's existing Azure/Qwen speech boundary."""

from __future__ import annotations

import html
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Literal

SpeechLanguage = Literal["Chinese", "English"]


@dataclass(frozen=True)
class TtsSynthesis:
    """Audio bytes and provider identity returned by a real configured TTS runtime."""

    audio: bytes
    provider: str
    model: str
    media_type: str


class TtsRuntimeError(RuntimeError):
    """A provider failure with a safe, non-secret user-facing message."""


class TtsRuntime:
    """Reuse the configured TTS providers for both interactive speech and videos.

    Calls are synchronous by design: the shared Worker owns cancellation points,
    timeout, retry budget and maximum per-clip text length around this boundary.
    """

    def __init__(self) -> None:
        self.qwen_url = os.getenv("QWEN_TTS_URL", "http://127.0.0.1:8020").rstrip("/")
        self.provider = os.getenv("TTS_PROVIDER", "auto").lower()
        self.azure_key = os.getenv("AZURE_SPEECH_KEY", "")
        self.azure_region = os.getenv("AZURE_SPEECH_REGION", "")
        self.azure_chinese_voice = os.getenv("AZURE_SPEECH_VOICE", "zh-CN-XiaoxiaoNeural")
        self.azure_english_voice = os.getenv("AZURE_SPEECH_ENGLISH_VOICE", "en-US-JennyNeural")

    def synthesize(
        self,
        text: str,
        *,
        language: SpeechLanguage = "Chinese",
        speaker: str | None = None,
        instruct: str = "",
        timeout_seconds: float | None = None,
    ) -> TtsSynthesis:
        """Synthesize bounded text and report the actual provider/model metadata."""
        clean = text.strip()
        if not clean or len(clean) > 2_000:
            raise ValueError("TTS 文本长度必须在 1 到 2000 字符之间")
        if len(speaker or "") > 40 or len(instruct) > 200:
            raise ValueError("TTS speaker 或 instruct 超出既有边界")
        timeout = max(1.0, min(timeout_seconds or 180.0, 180.0))
        if self.provider in {"auto", "azure"} and self.azure_key and self.azure_region:
            voice = self.azure_english_voice if language == "English" else self.azure_chinese_voice
            return self._synthesize_azure(clean, voice=voice, language=language, timeout=timeout)
        if self.provider == "azure":
            raise TtsRuntimeError("Azure Speech 未配置")
        metadata = self._qwen_metadata(language)
        body = json.dumps({
            "text": clean,
            "speaker": speaker or ("Ryan" if language == "English" else "Serena"),
            "instruct": instruct,
            "language": language,
        }, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.qwen_url}/tts",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                audio = response.read(20_000_001)
        except (OSError, urllib.error.URLError) as error:
            raise TtsRuntimeError("Qwen3-TTS 调用失败") from error
        if not audio or len(audio) > 20_000_000:
            raise TtsRuntimeError("Qwen3-TTS 返回音频为空或超过 20 MB 上限")
        return TtsSynthesis(
            audio=audio,
            provider="qwen3-tts",
            model=metadata["model"],
            media_type="audio/wav",
        )

    def _qwen_metadata(self, language: SpeechLanguage) -> dict[str, str]:
        """Read the loaded service identity before recording provenance or speaking English."""
        try:
            with urllib.request.urlopen(f"{self.qwen_url}/health", timeout=2) as response:
                payload = json.loads(response.read(16_384).decode("utf-8"))
        except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as error:
            raise TtsRuntimeError("Qwen3-TTS 健康信息不可用，无法记录实际模型版本") from error
        model = str(payload.get("model") or "").strip()
        if not model:
            raise TtsRuntimeError("Qwen3-TTS 未报告实际模型版本")
        if language == "English" and "English" not in payload.get("supportedLanguages", []):
            raise TtsRuntimeError("当前 Qwen3-TTS 服务未声明英语支持，请更新并重载语音服务后重试")
        return {"model": model}

    @staticmethod
    def _synthesize_azure(text: str, *, voice: str, language: SpeechLanguage, timeout: float) -> TtsSynthesis:
        region = os.getenv("AZURE_SPEECH_REGION", "")
        key = os.getenv("AZURE_SPEECH_KEY", "")
        if not key or not region:
            raise TtsRuntimeError("Azure Speech 未配置")
        ssml = (
            '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            'xmlns:mstts="https://www.w3.org/2001/mstts" '
            f'xml:lang="{"en-US" if language == "English" else "zh-CN"}">'
            f'<voice name="{html.escape(voice, quote=True)}">'
            f'<prosody rate="-5%">{html.escape(text)}</prosody>'
            "</voice></speak>"
        ).encode("utf-8")
        request = urllib.request.Request(
            f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
            data=ssml,
            headers={
                "Ocp-Apim-Subscription-Key": key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                "User-Agent": "dotty-tutor",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                audio = response.read(20_000_001)
        except (OSError, urllib.error.URLError) as error:
            raise TtsRuntimeError("Azure Speech 调用失败") from error
        if not audio or len(audio) > 20_000_000:
            raise TtsRuntimeError("Azure Speech 返回音频为空或超过 20 MB 上限")
        return TtsSynthesis(audio=audio, provider="azure-speech-neural", model=voice, media_type="audio/mpeg")
