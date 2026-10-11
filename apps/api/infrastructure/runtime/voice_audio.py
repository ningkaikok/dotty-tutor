"""Validate recorded audio before passing it to a decoder or remote STT."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

MAX_AUDIO_BYTES = 16 * 1024 * 1024
MAX_AUDIO_SECONDS = 60.0

_CONTAINERS = {
    "audio/flac": ("flac", b"fLaC"),
    "audio/mp3": ("mp3", None),
    "audio/mpeg": ("mp3", None),
    "audio/mpga": ("mp3", None),
    "audio/mp4": ("mov", None),
    "video/mp4": ("mov", None),
    "audio/ogg": ("ogg", b"OggS"),
    "audio/wav": ("wav", b"RIFF"),
    "audio/x-wav": ("wav", b"RIFF"),
    "audio/webm": ("matroska", b"\x1a\x45\xdf\xa3"),
}


def validate_audio_file(
    audio_file: Path, *, content_type: str | None = None
) -> tuple[float, str]:
    """Require a supported audio container and a finite, bounded duration.

    The demuxer is selected from the allowlist and FFmpeg protocols are limited
    to local files and pipes, so playlist-like uploads cannot redirect decoders
    to network URLs or arbitrary nested inputs.
    """
    audio_file = audio_file.resolve(strict=True)
    if (
        not audio_file.is_file()
        or audio_file.stat().st_size <= 0
        or audio_file.stat().st_size > MAX_AUDIO_BYTES
    ):
        raise ValueError("录音为空或超过 16 MB")

    media_type = content_type.split(";", 1)[0].strip().lower() if content_type else None
    if media_type is None:
        candidates = list(_CONTAINERS.items())
    else:
        container = _CONTAINERS.get(media_type)
        if container is None:
            raise ValueError("不支持此录音格式")
        candidates = [(media_type, container)]

    with audio_file.open("rb") as source:
        header = source.read(16)
    selected: tuple[str, tuple[str, bytes | None]] | None = None
    for candidate in candidates:
        _, (demuxer, magic) = candidate
        if magic is not None and header.startswith(magic):
            selected = candidate
            break
        if demuxer == "mov" and len(header) >= 8 and header[4:8] == b"ftyp":
            selected = candidate
            break
        if demuxer == "mp3" and (
            header.startswith(b"ID3")
            or (len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0)
        ):
            selected = candidate
            break
        if demuxer == "wav" and header.startswith(b"RIFF") and header[8:12] == b"WAVE":
            selected = candidate
            break
    if selected is None:
        raise ValueError("录音内容与所选媒体格式不符，或容器不受支持")

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe is required for bounded audio duration checks")
    path_env = os.environ.get("PATH", "")
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-f",
            selected[1][0],
            "-show_entries",
            "format=duration",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(audio_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=8,
        env={"PATH": path_env, "LANG": "C", "LC_ALL": "C"},
    )
    if result.returncode != 0 or len(result.stdout) > 16 * 1024:
        raise ValueError("ffprobe could not read the recording")

    try:
        probe = json.loads(result.stdout)
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("ffprobe returned invalid audio metadata") from error
    streams = probe.get("streams", [])
    if not any(
        isinstance(stream, dict) and stream.get("codec_type") == "audio"
        for stream in streams
    ):
        raise ValueError("recording does not contain an audio track")
    raw_duration = probe.get("format", {}).get("duration")
    try:
        duration = float(raw_duration)
    except (ValueError, TypeError):
        # Browser MediaRecorder WebM often omits container duration. Decode at
        # a low fixed sample rate and stop after 60.001 seconds to measure it
        # without trusting a claimed duration or allocating unbounded PCM.
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError(
                "ffmpeg is required when recording duration metadata is missing"
            )
        decoded = subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-f",
                selected[1][0],
                "-i",
                str(audio_file),
                "-map",
                "0:a:0",
                "-t",
                "60.001",
                "-vn",
                "-sn",
                "-dn",
                "-ac",
                "1",
                "-ar",
                "8000",
                "-f",
                "s16le",
                "pipe:1",
            ],
            check=False,
            capture_output=True,
            timeout=15,
            env={"PATH": path_env, "LANG": "C", "LC_ALL": "C"},
        )
        if (
            decoded.returncode != 0
            or not decoded.stdout
            or len(decoded.stdout) > 960_016
        ):
            raise ValueError("recording duration could not be bounded")
        duration = len(decoded.stdout) / 16_000
    if not (0 < duration <= MAX_AUDIO_SECONDS):
        raise ValueError("recording must be between 0 and 60 seconds")
    return duration, selected[1][0]
