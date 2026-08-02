"""Small local HTTP service for Qwen3-TTS.

Run it with the dedicated Python 3.12 environment:
    ../.qwen3-tts-venv/bin/python qwen_tts_service.py
"""

from __future__ import annotations

import io
import os
import time
from functools import lru_cache
from threading import Lock
from typing import Any

import soundfile as sf
import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from qwen_tts import Qwen3TTSModel

from observability import log_event


app = FastAPI(title="Qwen3-TTS local service")
MODEL_NAME = os.getenv("QWEN_TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice")
DEFAULT_SPEAKER = os.getenv("QWEN_TTS_SPEAKER", "Serena")
DEFAULT_INSTRUCT = os.getenv(
    "QWEN_TTS_INSTRUCT",
    "用耐心、清晰、自然的中文老师语气朗读，语速稍慢，重点处有轻微停顿。",
)


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2_000)
    speaker: str = Field(default=DEFAULT_SPEAKER, max_length=40)
    instruct: str = Field(default=DEFAULT_INSTRUCT, max_length=200)


_generation_lock = Lock()


def _device() -> str:
    configured = os.getenv("QWEN_TTS_DEVICE", "auto").lower()
    if configured != "auto":
        return configured
    if torch.cuda.is_available():
        return "cuda:0"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


@lru_cache(maxsize=1)
def get_model() -> tuple[Qwen3TTSModel, str]:
    device = _device()
    dtype = torch.float32 if device in {"cpu", "mps"} else torch.bfloat16
    kwargs: dict[str, Any] = {"device_map": device, "dtype": dtype}
    # FlashAttention is CUDA-only; omitting it keeps the service usable on Apple Silicon.
    model = Qwen3TTSModel.from_pretrained(MODEL_NAME, **kwargs)
    return model, device


@lru_cache(maxsize=128)
def _synthesize_audio(text: str, speaker: str, instruct: str) -> bytes:
    """Generate once per narration and reuse audio during lesson replay."""
    with _generation_lock:
        model, _device_name = get_model()
        wavs, sample_rate = model.generate_custom_voice(
            text=text,
            language="Chinese",
            speaker=speaker,
            instruct=instruct,
        )
        output = io.BytesIO()
        sf.write(output, wavs[0], sample_rate, format="WAV")
        return output.getvalue()


@app.on_event("startup")
def warm_model() -> None:
    """Load weights during service startup instead of delaying the first lesson."""
    started = time.perf_counter()
    try:
        get_model()
        log_event(
            "tts.service.warmed",
            level=10,
            device=_device(),
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
    except Exception as error:  # noqa: BLE001 - health endpoint will expose the failure
        log_event(
            "tts.service.warm.failed",
            level=40,
            error_type=type(error).__name__,
            error=str(error)[:300],
            exc_info=True,
        )


@app.get("/health")
def health() -> dict[str, Any]:
    payload = {
        "provider": "qwen3-tts",
        "model": MODEL_NAME,
        "device": _device(),
        "loaded": get_model.cache_info().currsize > 0,
    }
    log_event("tts.service.health", level=10, device=payload["device"], loaded=payload["loaded"])
    return payload


@app.post("/tts")
def tts(request: TtsRequest) -> Response:
    started = time.perf_counter()
    try:
        before = _synthesize_audio.cache_info().hits
        audio = _synthesize_audio(request.text.strip(), request.speaker, request.instruct)
        cache_hit = _synthesize_audio.cache_info().hits > before
        log_event(
            "tts.service.request.completed",
            text_length=len(request.text),
            audio_bytes=len(audio),
            cache_hit=cache_hit,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return Response(content=audio, media_type="audio/wav")
    except Exception as error:  # noqa: BLE001 - surface model errors to the proxy
        log_event(
            "tts.service.request.failed",
            level=40,
            text_length=len(request.text),
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            error_type=type(error).__name__,
            error=str(error)[:300],
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Qwen3-TTS 生成失败：{error}") from error


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("QWEN_TTS_PORT", "8020")))
