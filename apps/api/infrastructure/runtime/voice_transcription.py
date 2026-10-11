"""Optional transcription boundary for recorded student audio.

The runtime has no built-in or silent fallback: without explicit credentials,
recordings remain playable and students can enter and confirm their own text.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from infrastructure.runtime.voice_audio import MAX_AUDIO_BYTES, validate_audio_file
from observability import log_event

ALLOWED_AUDIO_TYPES = {
    "audio/flac": ".flac",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/mpga": ".mpga",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/webm": ".webm",
    "video/mp4": ".mp4",
}


class TranscriptionUnavailable(RuntimeError):
    """The optional STT provider is not configured or did not return a result."""


class VoiceTranscriptionRuntime:
    """Call an OpenAI-compatible audio transcription endpoint when configured."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        local_python: str | None = None,
        local_model_path: str | None = None,
        local_device: str | None = None,
        local_language: str | None = None,
        timeout: float = 45.0,
    ) -> None:
        self.api_key = (
            api_key
            if api_key is not None
            else os.getenv("VOICE_STT_API_KEY", "").strip()
        )
        self.base_url = (
            base_url or os.getenv("VOICE_STT_BASE_URL", "https://api.openai.com/v1")
        ).rstrip("/")
        self.model = model or os.getenv("VOICE_STT_MODEL", "gpt-4o-mini-transcribe")
        self.local_python = (
            local_python
            if local_python is not None
            else os.getenv("VOICE_STT_LOCAL_PYTHON", "").strip()
        )
        self.local_model_path = (
            local_model_path
            if local_model_path is not None
            else os.getenv("VOICE_STT_LOCAL_MODEL_PATH", "").strip()
        )
        self.local_device = (
            local_device or os.getenv("VOICE_STT_LOCAL_DEVICE", "cpu").strip().lower()
        )
        configured_language = (
            (local_language or os.getenv("VOICE_STT_LOCAL_LANGUAGE", ""))
            .strip()
            .lower()
        )
        self.local_language = (
            configured_language if configured_language in {"en", "zh"} else None
        )
        self.timeout = max(5.0, min(timeout, 150.0))

    @property
    def available(self) -> bool:
        return self.local_available or bool(self.api_key)

    @property
    def local_available(self) -> bool:
        return bool(
            self.local_python
            and Path(self.local_python).is_file()
            and os.access(self.local_python, os.X_OK)
            and self.local_model_path
            and Path(self.local_model_path).is_dir()
        )

    @property
    def provider(self) -> str | None:
        if self.local_available:
            return "local-whisper"
        if self.api_key:
            return "openai-compatible"
        return None

    def transcribe(
        self, *, audio: bytes, filename: str, content_type: str
    ) -> dict[str, Any]:
        """Return provider text and raw confidence diagnostics without persisting audio."""
        if not self.available:
            raise TranscriptionUnavailable(self.unavailable_detail)
        if not audio or len(audio) > MAX_AUDIO_BYTES:
            raise ValueError("录音为空或超过 16 MB")
        media_type = content_type.split(";", 1)[0].strip().lower()
        extension = ALLOWED_AUDIO_TYPES.get(media_type)
        if not extension:
            raise ValueError(
                "浏览器录音格式暂不支持，请改用 WebM、WAV、M4A、MP3、OGG 或 FLAC"
            )
        with tempfile.NamedTemporaryFile(
            prefix="dotty-voice-", suffix=extension, delete=False
        ) as temporary:
            temporary.write(audio)
            audio_path = Path(temporary.name)
        try:
            validate_audio_file(audio_path, content_type=media_type)
            if self.local_available:
                try:
                    return self._transcribe_local(audio_path=audio_path)
                except TranscriptionUnavailable:
                    if not self.api_key:
                        raise
            return self._transcribe_cloud(
                audio=audio,
                filename=filename,
                content_type=media_type,
                extension=extension,
            )
        finally:
            audio_path.unlink(missing_ok=True)

    @property
    def unavailable_detail(self) -> str:
        if self.local_python or self.local_model_path:
            return "本地 Whisper 模型或 Python 运行环境未就绪；也未配置服务端转写凭据，可手动输入文字"
        return (
            "本地 Whisper 未配置，且服务端转写凭据缺失；可使用浏览器转写或手动输入文字"
        )

    def _transcribe_local(self, *, audio_path: Path) -> dict[str, Any]:
        script = Path(__file__).with_name("local_whisper_stt.py").resolve()
        if not self.local_python or not self.local_model_path:
            raise TranscriptionUnavailable(self.unavailable_detail)
        command = [
            self.local_python,
            str(script),
            "--audio-file",
            str(audio_path),
            "--model-path",
            self.local_model_path,
            "--device",
            self.local_device,
        ]
        if self.local_language:
            command.extend(["--language", self.local_language])
        # The optional model runner must not inherit unrelated service credentials.
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "LANG": "C",
            "LC_ALL": "C",
        }
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=environment,
            )
        except subprocess.TimeoutExpired as error:
            raise TranscriptionUnavailable(
                "本地语音转写超过时限，请手动输入文字"
            ) from error
        if result.returncode != 0 or len(result.stdout) > 64 * 1024:
            log_event(
                "voice.transcription.failed",
                level=30,
                provider="local-whisper",
                error_type="LocalWhisperProcessError",
            )
            raise TranscriptionUnavailable("本地语音转写暂不可用，请手动输入文字")
        try:
            payload = json.loads(result.stdout.strip())
        except ValueError as error:
            raise TranscriptionUnavailable(
                "本地语音转写返回格式无效，请手动输入文字"
            ) from error
        transcript = payload.get("text") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("provider") != "local-whisper"
            or not isinstance(transcript, str)
        ):
            raise TranscriptionUnavailable("本地语音转写返回格式无效，请手动输入文字")
        text = transcript.strip()
        if not text or len(text) > 5000:
            raise TranscriptionUnavailable("本地语音转写没有返回可用文字，请手动输入")
        return {
            "text": text,
            "confidence": None,
            "ambiguityFlags": [
                "confidence_unavailable",
                *(["short_transcription"] if len(text) <= 3 else []),
            ],
            "needsConfirmation": True,
        }

    def _transcribe_cloud(
        self,
        *,
        audio: bytes,
        filename: str,
        content_type: str,
        extension: str,
    ) -> dict[str, Any]:
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename.rsplit("/", 1)[-1])[:100]
        if not safe_name or "." not in safe_name:
            safe_name = f"recording{extension}"

        boundary = f"----dottyVoice{uuid.uuid4().hex}"
        fields = [
            ("model", self.model),
            ("response_format", "json"),
            ("include[]", "logprobs"),
        ]
        chunks: list[bytes] = []
        for name, value in fields:
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                    value.encode("utf-8"),
                    b"\r\n",
                ]
            )
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'.encode(),
                f"Content-Type: {content_type}\r\n\r\n".encode(),
                audio,
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ]
        )
        request = urllib.request.Request(
            urljoin(f"{self.base_url}/", "audio/transcriptions"),
            data=b"".join(chunks),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read(1024 * 1024 + 1))
        except (
            OSError,
            urllib.error.URLError,
            urllib.error.HTTPError,
            ValueError,
        ) as error:
            log_event(
                "voice.transcription.failed",
                level=30,
                provider="openai-compatible-transcription",
                model=self.model,
                error_type=type(error).__name__,
            )
            raise TranscriptionUnavailable(
                "语音转写服务暂不可用，请手动检查并输入文字"
            ) from error

        transcript = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(transcript, str) or not transcript.strip():
            raise TranscriptionUnavailable("转写服务没有识别出文字，请手动输入")
        logprobs = payload.get("logprobs") if isinstance(payload, dict) else None
        values = [
            float(item["logprob"])
            for item in logprobs or []
            if isinstance(item, dict) and isinstance(item.get("logprob"), (int, float))
        ]
        confidence = None
        ambiguity_flags: list[str] = []
        if values:
            confidence = {
                "kind": "token_logprobs",
                "meanLogprob": round(sum(values) / len(values), 4),
                "lowConfidenceTokenCount": sum(value < -1.0 for value in values),
                "tokenCount": len(values),
            }
            if confidence["lowConfidenceTokenCount"]:
                ambiguity_flags.append("model_low_confidence_tokens")
        else:
            ambiguity_flags.append("confidence_unavailable")
        if len(transcript.strip()) <= 3:
            ambiguity_flags.append("short_transcription")
        log_event(
            "voice.transcription.completed",
            provider="openai-compatible-transcription",
            model=self.model,
            audio_bytes=len(audio),
            transcript_chars=len(transcript.strip()),
        )
        return {
            "text": transcript.strip()[:5000],
            "confidence": confidence,
            "ambiguityFlags": ambiguity_flags,
            "needsConfirmation": True,
        }
