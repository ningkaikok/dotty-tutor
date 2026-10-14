"""Render reviewed storyboard data through a fixed CairoSVG and FFmpeg pipeline."""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from application.job_worker import JobCancelled, TerminalJobError

if TYPE_CHECKING:
    from types import ModuleType

WIDTH = 1_280
HEIGHT = 720
FPS = 25
MIN_DURATION_SECONDS = 60.0
MAX_DURATION_SECONDS = 120.0
MAX_AUDIO_BYTES = 20_000_000
MAX_VIDEO_BYTES = 100_000_000
MAX_RENDER_SECONDS = 600.0
FONT_LINUX = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_MACOS = "/System/Library/Fonts/Hiragino Sans GB.ttc"
MATH_FONT_MACOS = "/System/Library/Fonts/Supplemental/STIXTwoMath.otf"
MATH_SYMBOLS = frozenset("×÷≤≥√=+-−<>%πθ→·≈≠")
SUPPORTED_MATH_SYMBOLS = frozenset("×÷≤≥√=+-−<>%πθ→·≈≠")
SUPPORTED_TEX = {
    r"\times": "×",
    r"\cdot": "·",
    r"\div": "÷",
    r"\le": "≤",
    r"\leq": "≤",
    r"\ge": "≥",
    r"\geq": "≥",
    r"\neq": "≠",
    r"\approx": "≈",
    r"\pi": "π",
    r"\theta": "θ",
    r"\rightarrow": "→",
    r"\to": "→",
    r"\%": "%",
}
_TEX_TOKEN = re.compile(r"\\[A-Za-z]+|\\.")


def _load_cairosvg() -> ModuleType:
    """Import CairoSVG only when a video is actually rendered.

    cairocffi loads the native Cairo library at import time. Importing it at module level
    made the whole API fail to start on hosts without Cairo (e.g. a Windows dev machine),
    even though only the video worker needs it. A missing library now fails just this job.
    """
    try:
        import cairosvg
    except (ImportError, OSError) as error:
        raise TerminalJobError(f"教学视频渲染环境未就绪：无法加载 CairoSVG/Cairo（{error}）") from error
    return cairosvg


@dataclass(frozen=True)
class RenderedLessonMedia:
    """Temporary render files and verification data before immutable storage."""

    video_path: Path
    captions_path: Path
    duration_seconds: float
    renderer_snapshot: dict[str, Any]
    consistency_review: dict[str, Any]
    tts_metadata: list[dict[str, str]]


def normalize_speech_text(value: str) -> str:
    """Convert basic Markdown and supported inline math to plain, spoken text.

    Unknown TeX commands are rejected. This deliberately favors an explicit
    teacher edit over silently deleting conditions or mathematical symbols.
    """
    if value.count("$") % 2:
        raise TerminalJobError("公式分隔符不完整，请先在分镜中修订")
    text = value.replace("$$", " ").replace("$", " ").replace("−", "-")
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    if re.search(r"(?<=[A-Za-z0-9])_(?:\{[^}]*\}|[A-Za-z0-9])", text):
        raise TerminalJobError("当前视频渲染不支持公式下标，请在分镜中写出可读文字后重试")
    text = re.sub(r"[`*_>#]", " ", text)

    def replace_tex(match: re.Match[str]) -> str:
        command = match.group(0)
        replacement = SUPPORTED_TEX.get(command)
        if replacement is None:
            raise TerminalJobError(f"当前视频渲染不支持公式符号 {command}，请改成可读文本后重试")
        return replacement

    text = _TEX_TOKEN.sub(replace_tex, text)
    if "\\" in text or "{" in text or "}" in text:
        raise TerminalJobError("当前视频渲染不支持复杂公式语法，请在分镜中写出可读符号后重试")
    if "^" in text or "_" in text:
        raise TerminalJobError("当前视频渲染不支持公式上下标，请在分镜中写出可读文字后重试")
    unsupported_math = next(
        (character for character in text if unicodedata.category(character) == "Sm" and character not in SUPPORTED_MATH_SYMBOLS),
        None,
    )
    if unsupported_math:
        raise TerminalJobError(f"当前视频渲染不支持公式符号 U+{ord(unsupported_math):04X}，请先修订片段")
    return re.sub(r"\s+", " ", text).strip()


def _font_family() -> str:
    if Path(FONT_LINUX).is_file():
        return "Noto Sans CJK SC"
    if Path(FONT_MACOS).is_file():
        return "Hiragino Sans GB"
    return "sans-serif"


def _svg_text(value: str) -> str:
    """Give math and prose explicit font runs so Cairo cannot leak a fallback."""
    text_family = html.escape(_font_family(), quote=True)
    math_family = "STIX Two Math" if Path(MATH_FONT_MACOS).is_file() else "DejaVu Sans"
    parts: list[str] = []
    active_kind: str | None = None
    active_text: list[str] = []

    def flush() -> None:
        if not active_text or active_kind is None:
            return
        family = math_family if active_kind == "math" else text_family
        content = "".join(html.escape(character) for character in active_text)
        parts.append(f'<tspan font-family="{family}">{content}</tspan>')

    for character in value:
        kind = "math" if character in MATH_SYMBOLS else "text"
        if active_kind is not None and kind != active_kind:
            flush()
            active_text.clear()
        active_kind = kind
        active_text.append(character)
    flush()
    return "".join(parts)


def _wrap_text(text: str, *, max_chars: int, max_lines: int) -> list[str]:
    """Wrap Chinese text by glyph count and English text at word boundaries."""
    lines: list[str] = []
    for paragraph in text.splitlines() or [text]:
        remaining = paragraph.strip()
        while remaining:
            if len(remaining) <= max_chars:
                lines.append(remaining)
                break
            split_at = max_chars
            for match in re.finditer(r"\s+", remaining[: max_chars + 1]):
                split_at = match.start()
            if split_at < max_chars // 2:
                split_at = max_chars
            lines.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()
            if len(lines) > max_lines:
                raise TerminalJobError("分镜文字超出画面安全区域，请缩短本片段后再渲染")
    if len(lines) > max_lines:
        raise TerminalJobError("分镜文字超出画面安全区域，请缩短本片段后再渲染")
    return lines


def build_scene_svg(scene: dict[str, Any], source: dict[str, Any], *, index: int) -> str:
    """Build one self-contained SVG using escaped text and no active/external content."""
    title_lines = _wrap_text(str(scene["title"]), max_chars=28, max_lines=2)
    narration = str(scene["narration"])
    body_lines = _wrap_text(narration, max_chars=40, max_lines=7)
    if not title_lines or not body_lines:
        raise TerminalJobError("分镜标题和讲解不能为空")
    title_svg = "".join(
        f'<text x="96" y="{172 + row * 44}" class="title">{_svg_text(line)}</text>'
        for row, line in enumerate(title_lines)
    )
    body_svg = "".join(
        f'<text x="112" y="{282 + row * 31}" class="body">{_svg_text(line)}</text>'
        for row, line in enumerate(body_lines)
    )
    source_title = str(source.get("sourceTitle") or "教材来源")
    page_numbers = sorted({int(ref["page"]) for ref in scene.get("sourceRefs", []) if ref.get("page")})
    page_label = "、".join(str(page) for page in page_numbers) if page_numbers else "待复核页码"
    attribution = str(source.get("attributionText") or "")
    credit_parts = [f"来源：{source_title} · 第 {page_label} 页"]
    if source.get("sourceUrl"):
        credit_parts.append(f"链接：{source['sourceUrl']}")
    if source.get("license"):
        credit_parts.append(f"许可：{source['license']}")
    if attribution:
        credit_parts.append(attribution)
    credit_lines = _wrap_text(" · ".join(credit_parts), max_chars=84, max_lines=5)
    if not credit_lines:
        raise TerminalJobError("来源归属信息不能为空")
    credit_svg = "".join(
        f'<text x="116" y="{548 + row * 17}" class="cite">{_svg_text(line)}</text>'
        for row, line in enumerate(credit_lines)
    )
    full_chapter_title = str(source.get("chapterTitle") or "课程讲解")
    # This is a visual eyebrow only; the full title remains in the immutable source snapshot.
    chapter_title = full_chapter_title if len(full_chapter_title) <= 32 else f"{full_chapter_title[:31]}…"
    scene_total = len(source.get("scenes") or [])
    if not 1 <= scene_total <= 8:
        raise TerminalJobError("分镜总数超出画面标记上限")
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">
<rect width="1280" height="720" fill="#f4f7fb"/>
<circle cx="1168" cy="88" r="170" fill="#dceeff"/>
<rect x="54" y="52" width="1172" height="616" rx="34" fill="#ffffff" stroke="#d9e3ef" stroke-width="2"/>
<rect x="88" y="91" width="8" height="38" rx="4" fill="#1677ff"/>
<text x="116" y="120" class="eyebrow">{_svg_text(chapter_title)}</text>
<text x="1120" y="120" text-anchor="end" class="eyebrow">片段 {index + 1}</text>
{title_svg}
<line x1="96" y1="240" x2="1184" y2="240" stroke="#e7edf5" stroke-width="2"/>
{body_svg}
<rect x="96" y="524" width="1088" height="112" rx="16" fill="#edf5ff"/>
{credit_svg}
<text x="1184" y="656" text-anchor="end" class="counter">{index + 1:02d} / {scene_total:02d}</text>
<style>
text {{ font-family: '{_font_family()}', 'Noto Sans CJK SC', 'PingFang SC', sans-serif; fill: #172b4d; }}
.eyebrow {{ font-size: 20px; font-weight: 600; fill: #526782; }}
.title {{ font-size: 39px; font-weight: 700; fill: #102a43; }}
.body {{ font-size: 24px; fill: #233b53; }}
.cite {{ font-size: 13px; fill: #526782; }}
.counter {{ font-size: 14px; fill: #8293a8; }}
</style>
</svg>'''


def _timestamp(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1_000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{millis:03d}"


def _caption_chunks(text: str, *, language: str) -> list[str]:
    chunks: list[str] = []
    max_chars = 52 if language == "English" else 26
    for sentence in re.split(r"(?<=[.!?。！？；;])\s*", text):
        remaining = sentence.strip()
        while remaining:
            if len(remaining) <= max_chars:
                chunks.append(remaining)
                break
            cut = max_chars
            if language == "English":
                boundary = remaining.rfind(" ", 0, max_chars + 1)
                if boundary >= max_chars // 2:
                    cut = boundary
            chunks.append(remaining[:cut].strip())
            remaining = remaining[cut:].strip()
    return chunks or [text]


def build_vtt(scenes: list[dict[str, Any]], durations: list[float], *, language: str) -> tuple[str, int]:
    """Create a readable VTT sidecar whose cue text preserves narration verbatim."""
    cues: list[str] = ["WEBVTT", ""]
    timeline = 0.0
    cue_index = 1
    for scene, duration in zip(scenes, durations, strict=True):
        chunks = _caption_chunks(scene["narration"], language=language)
        weights = [max(1, len(chunk.replace(" ", ""))) for chunk in chunks]
        total_weight = sum(weights)
        cursor = timeline
        for chunk, weight in zip(chunks, weights, strict=True):
            cue_end = cursor + duration * weight / total_weight
            safe_caption = html.escape(chunk, quote=False)
            cues.extend([str(cue_index), f"{_timestamp(cursor)} --> {_timestamp(cue_end)}", safe_caption, ""])
            cursor = cue_end
            cue_index += 1
        timeline += duration
    return "\n".join(cues), cue_index - 1


def _run_command(
    args: list[str], *, deadline: float, cancellation_check: Callable[[], bool], max_output_bytes: int = 2_000_000,
) -> subprocess.CompletedProcess[bytes]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("视频渲染超过 10 分钟时限")
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    while True:
        if cancellation_check():
            process.terminate()
            try:
                process.communicate(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            raise JobCancelled()
        try:
            stdout, stderr = process.communicate(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
            break
        except subprocess.TimeoutExpired:
            if time.monotonic() >= deadline:
                process.kill()
                process.communicate()
                raise TimeoutError("视频渲染超过 10 分钟时限")
    if len(stdout) + len(stderr) > max_output_bytes:
        raise TerminalJobError("渲染工具输出超过资源限制")
    completed = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    if completed.returncode != 0:
        safe_error = stderr.decode("utf-8", errors="replace")[-1_000:]
        raise TerminalJobError(f"媒体渲染失败：{safe_error}")
    return completed


class LessonMediaRenderer:
    """Fixed-size still-scene animation renderer with process, time, and output bounds."""

    def __init__(self, *, ffmpeg: str | None = None, ffprobe: str | None = None) -> None:
        self.ffmpeg = ffmpeg or shutil.which("ffmpeg") or ""
        self.ffprobe = ffprobe or shutil.which("ffprobe") or ""

    @staticmethod
    def validate_storyboard(*, scenes: list[dict[str, Any]], source: dict[str, Any]) -> None:
        """Reject text that cannot fit before any paid TTS synthesis begins."""
        if not 3 <= len(scenes) <= 8:
            raise TerminalJobError("视频分镜需要 3 到 8 个讲解片段")
        if sum(len(scene.get("narration", "")) for scene in scenes) > 8_000:
            raise TerminalJobError("讲解总长度超过 8000 字符上限")
        for index, scene in enumerate(scenes):
            if len(scene.get("narration", "")) > 2_000:
                raise TerminalJobError("单个分镜旁白超过 2000 字符上限")
            build_scene_svg(scene, {**source, "scenes": scenes}, index=index)

    def render(
        self,
        *,
        scenes: list[dict[str, Any]],
        source: dict[str, Any],
        audio: list[dict[str, Any]],
        language: str,
        temp_root: Path,
        cancellation_check: Callable[[], bool],
        deadline: float | None = None,
    ) -> RenderedLessonMedia:
        """Render one clip per reviewed scene, then join within a fixed deadline."""
        if not self.ffmpeg or not self.ffprobe:
            raise TerminalJobError("未安装 FFmpeg/FFprobe，无法渲染教学视频")
        if not 1 <= len(scenes) <= 8 or len(scenes) != len(audio):
            raise TerminalJobError("分镜数量超出 1 到 8 段上限或配音片段不完整")
        if sum(len(scene["narration"]) for scene in scenes) > 8_000:
            raise TerminalJobError("讲解总长度超过 8000 字符上限")
        deadline = deadline or time.monotonic() + MAX_RENDER_SECONDS
        cairosvg = _load_cairosvg()
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="lesson-media-", dir=temp_root) as work:
            workdir = Path(work)
            clip_paths: list[Path] = []
            durations: list[float] = []
            for index, (scene, speech) in enumerate(zip(scenes, audio, strict=True)):
                if cancellation_check():
                    raise JobCancelled()
                raw_audio = speech["audio"]
                if not isinstance(raw_audio, bytes) or not raw_audio or len(raw_audio) > MAX_AUDIO_BYTES:
                    raise TerminalJobError("TTS 音频为空或超过单片段 20 MB 上限")
                audio_path = workdir / f"audio-{index:02d}.{('wav' if speech.get('mediaType') == 'audio/wav' else 'mp3')}"
                audio_path.write_bytes(raw_audio)
                png_path = workdir / f"scene-{index:02d}.png"
                svg_bytes = build_scene_svg(scene, {**source, "scenes": scenes}, index=index).encode("utf-8")
                cairosvg.svg2png(bytestring=svg_bytes, write_to=str(png_path), output_width=WIDTH, output_height=HEIGHT)
                probe = _run_command(
                    [self.ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(audio_path)],
                    deadline=deadline,
                    cancellation_check=cancellation_check,
                )
                metadata = json.loads(probe.stdout.decode("utf-8"))
                duration = float(metadata["format"]["duration"])
                if duration <= 0 or duration > 90:
                    raise TerminalJobError("单个配音片段时长必须在 0 到 90 秒之间")
                durations.append(duration)
                clip_path = workdir / f"clip-{index:02d}.mp4"
                _run_command(
                    [
                        self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                        "-loop", "1", "-framerate", str(FPS), "-i", str(png_path), "-i", str(audio_path),
                        "-vf", "zoompan=z='min(zoom+0.0004,1.04)':d=1:s=1280x720:fps=25,format=yuv420p",
                        "-t", f"{duration:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                        "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(clip_path),
                    ],
                    deadline=deadline,
                    cancellation_check=cancellation_check,
                )
                clip_paths.append(clip_path)
            duration_total = sum(durations)
            consistency_issues: list[dict[str, str]] = []
            if not MIN_DURATION_SECONDS <= duration_total <= MAX_DURATION_SECONDS:
                consistency_issues.append({
                    "code": "duration_out_of_range",
                    "message": f"配音总时长 {duration_total:.2f} 秒不在 60 到 120 秒范围内",
                })
            concat_path = workdir / "concat.txt"
            concat_path.write_text("\n".join(f"file '{clip.as_posix().replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" for clip in clip_paths), encoding="utf-8")
            video_path = workdir / "lesson.mp4"
            _run_command(
                [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_path), "-c", "copy", "-movflags", "+faststart", str(video_path)],
                deadline=deadline,
                cancellation_check=cancellation_check,
            )
            if video_path.stat().st_size > MAX_VIDEO_BYTES:
                raise TerminalJobError("教学视频超过 100 MB 资源上限")
            captions_path = workdir / "lesson.vtt"
            captions, caption_count = build_vtt(scenes, durations, language=language)
            captions_path.write_text(captions, encoding="utf-8")
            if caption_count == 0:
                consistency_issues.append({"code": "captions_missing", "message": "未生成字幕侧车"})
            probe_video = _run_command(
                [self.ffprobe, "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(video_path)],
                deadline=deadline,
                cancellation_check=cancellation_check,
            )
            stream_types = {item.get("codec_type") for item in json.loads(probe_video.stdout.decode("utf-8")).get("streams", [])}
            if not {"video", "audio"}.issubset(stream_types):
                consistency_issues.append({"code": "audio_video_stream_missing", "message": "渲染产物缺少视频或音频流"})
            expected_refs = [ref for scene in scenes for ref in scene.get("sourceRefs", [])]
            if not expected_refs:
                consistency_issues.append({"code": "source_reference_missing", "message": "分镜缺少可审查来源定位"})
            consistency = {
                "passed": not consistency_issues,
                "issues": consistency_issues,
                "checkedAt": time.time(),
                "checks": {
                    "durationWithinBounds": MIN_DURATION_SECONDS <= duration_total <= MAX_DURATION_SECONDS,
                    "audioPresent": "audio" in stream_types,
                    "videoPresent": "video" in stream_types,
                    "captionsPresent": caption_count > 0,
                    "sourcesPresent": bool(expected_refs),
                },
            }
            renderer_snapshot = {
                "implementation": "CairoSVG+FFmpeg",
                "version": "lesson-media-renderer-v1",
                "cairoSvg": cairosvg.__version__,
                "ffmpeg": self._version(self.ffmpeg, deadline, cancellation_check),
                "width": WIDTH,
                "height": HEIGHT,
                "fps": FPS,
                "fontFamily": _font_family(),
                "animation": "ken-burns-zoom-pan-v1",
                "captionFormat": "WebVTT-sidecar-v1",
                "sceneCount": len(scenes),
                "tts": [speech.get("metadata", {}) for speech in audio],
            }
            final_video = temp_root / f"{uuid.uuid4().hex}.mp4"
            final_captions = temp_root / f"{uuid.uuid4().hex}.vtt"
            shutil.copyfile(video_path, final_video)
            shutil.copyfile(captions_path, final_captions)
            return RenderedLessonMedia(
                final_video, final_captions, duration_total, renderer_snapshot, consistency,
                [speech.get("metadata", {}) for speech in audio],
            )

    def _version(self, command: str, deadline: float, cancellation_check: Callable[[], bool]) -> str:
        result = _run_command([command, "-version"], deadline=deadline, cancellation_check=cancellation_check)
        return result.stdout.decode("utf-8", errors="replace").splitlines()[0][:120]
