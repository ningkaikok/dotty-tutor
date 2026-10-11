"""Bounded local Whisper CLI used by the optional voice transcription adapter.

Model files must already exist on disk. Loading is always offline so an API
request can never trigger a model download or execute repository code.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    from infrastructure.runtime.voice_audio import (
        MAX_AUDIO_BYTES,
        MAX_AUDIO_SECONDS,
        validate_audio_file,
    )
except ModuleNotFoundError:  # Executed as a standalone script by the runtime adapter.
    from voice_audio import MAX_AUDIO_BYTES, MAX_AUDIO_SECONDS, validate_audio_file

MAX_PCM_BYTES = 4 * 16000 * 60


def _decode_audio(audio_file: Path, demuxer: str) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required for local Whisper transcription")
    result = subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-f",
            demuxer,
            "-i",
            str(audio_file),
            "-t",
            str(MAX_AUDIO_SECONDS),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-fs",
            str(MAX_PCM_BYTES),
            "-f",
            "f32le",
            "pipe:1",
        ],
        check=False,
        capture_output=True,
        timeout=25,
        env={"PATH": os.environ.get("PATH", ""), "LANG": "C", "LC_ALL": "C"},
    )
    if result.returncode != 0:
        raise RuntimeError("ffmpeg could not decode the recording")
    if not result.stdout or len(result.stdout) > MAX_PCM_BYTES:
        raise ValueError("decoded recording is empty or exceeds the audio limit")
    return result.stdout


def transcribe(args: argparse.Namespace) -> dict[str, object]:
    started = time.perf_counter()
    audio_file = args.audio_file.expanduser().resolve(strict=True)
    model_path = args.model_path.expanduser().resolve(strict=True)
    if not audio_file.is_file() or audio_file.stat().st_size > MAX_AUDIO_BYTES:
        raise ValueError("recording must be a file no larger than 16 MB")
    model_config_path = model_path / "config.json"
    weights_path = model_path / "model.safetensors"
    if (
        not model_path.is_dir()
        or not model_config_path.is_file()
        or not weights_path.is_file()
    ):
        raise ValueError("the local openai/whisper-tiny model files are incomplete")
    if model_config_path.stat().st_size > 64 * 1024:
        raise ValueError("the local Whisper model config is too large")
    config = json.loads(model_config_path.read_text(encoding="utf-8"))
    if not (
        isinstance(config, dict)
        and config.get("_name_or_path") == "openai/whisper-tiny"
        and config.get("model_type") == "whisper"
        and config.get("architectures") == ["WhisperForConditionalGeneration"]
        and config.get("d_model") == 384
        and config.get("encoder_layers") == 4
        and config.get("decoder_layers") == 4
    ):
        raise ValueError(
            "the model directory must be a local openai/whisper-tiny mirror"
        )
    _duration, demuxer = validate_audio_file(audio_file)
    decoded = _decode_audio(audio_file, demuxer)

    # Runtime dependencies come from the configured local Python environment;
    # keep their initialization chatter on stderr and reserve stdout for JSON.
    numpy: Any = importlib.import_module("numpy")
    torch: Any = importlib.import_module("torch")
    transformers: Any = importlib.import_module("transformers")
    WhisperForConditionalGeneration = getattr(
        transformers, "WhisperForConditionalGeneration"
    )
    WhisperProcessor = getattr(transformers, "WhisperProcessor")

    samples = numpy.frombuffer(decoded, dtype=numpy.float32)
    with contextlib.redirect_stdout(sys.stderr):
        processor = WhisperProcessor.from_pretrained(
            str(model_path), local_files_only=True
        )
        model = WhisperForConditionalGeneration.from_pretrained(
            str(model_path), local_files_only=True, use_safetensors=True
        )
        model.to(args.device)
        processed = processor(
            samples,
            sampling_rate=16000,
            return_tensors="pt",
            return_attention_mask=True,
        )
        features = processed.input_features.to(args.device)
        attention_mask = processed.get("attention_mask")
        if attention_mask is None:
            attention_mask = torch.ones(
                (features.shape[0], features.shape[-1]),
                dtype=torch.long,
                device=args.device,
            )
        else:
            attention_mask = attention_mask.to(args.device)
        with torch.inference_mode():
            output = model.generate(
                features,
                attention_mask=attention_mask,
                language=args.language,
                task="transcribe",
                do_sample=False,
                max_new_tokens=192,
            )
        text = processor.batch_decode(output, skip_special_tokens=True)[0].strip()
    if not text or len(text) > 5000:
        raise ValueError("Whisper returned no text or an overlong transcript")
    return {
        "text": text,
        "language": args.language or "auto",
        "confidence": None,
        "provider": "local-whisper",
        "model": "openai/whisper-tiny",
        "elapsedMs": round((time.perf_counter() - started) * 1000, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-file", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--language", choices=("en", "zh"))
    parser.add_argument("--device", choices=("cpu", "mps", "cuda"), default="cpu")
    args = parser.parse_args()
    try:
        result = transcribe(args)
    except Exception as error:  # CLI boundary: one sanitized failure line on stderr.
        print(f"local Whisper failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
