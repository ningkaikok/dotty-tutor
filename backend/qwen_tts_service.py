"""Small local HTTP service for Qwen3-TTS.

Run it with the dedicated Python 3.12 environment:
    ../.qwen3-tts-venv/bin/python qwen_tts_service.py
"""

from __future__ import annotations

import io
import os
from functools import lru_cache
from typing import Any

import soundfile as sf
import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from qwen_tts import Qwen3TTSModel


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


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "provider": "qwen3-tts",
        "model": MODEL_NAME,
        "device": _device(),
        "loaded": get_model.cache_info().currsize > 0,
    }


@app.post("/tts")
def tts(request: TtsRequest) -> Response:
    try:
        model, _device_name = get_model()
        wavs, sample_rate = model.generate_custom_voice(
            text=request.text,
            language="Chinese",
            speaker=request.speaker,
            instruct=request.instruct,
        )
        output = io.BytesIO()
        sf.write(output, wavs[0], sample_rate, format="WAV")
        return Response(content=output.getvalue(), media_type="audio/wav")
    except Exception as error:  # noqa: BLE001 - surface model errors to the proxy
        raise HTTPException(status_code=500, detail=f"Qwen3-TTS 生成失败：{error}") from error


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("QWEN_TTS_PORT", "8020")))
