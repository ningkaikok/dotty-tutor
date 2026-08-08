"""Pure helpers for turning OCR Markdown into bounded question sources."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


QUESTION_START_PATTERN = re.compile(r"(?m)^\s*(?P<number>\d{1,3})[.．、]\s*")
MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
MAX_QUESTIONS_PER_BATCH = 5


def safe_text(value: Any, fallback: str, limit: int = 600) -> str:
    """Normalize untrusted model output and apply a hard storage limit."""
    text = str(value or "").strip()
    return (text or fallback)[:limit]


def safe_string_list(value: Any, fallback: list[str], limit: int = 8) -> list[str]:
    """Normalize a model-produced list without accepting nested structures."""
    if not isinstance(value, list):
        return fallback
    items = [safe_text(item, "", 160) for item in value]
    items = [item for item in items if item]
    return items[:limit] or fallback


def split_question_sources(source: str) -> list[tuple[str, str, list[str]]]:
    """Split OCR Markdown into complete numbered questions before answers."""
    question_area = re.split(r"(?m)^\s*#*\s*(?:参考答案|答案|解析)\s*$", source, maxsplit=1)[0]
    matches = list(QUESTION_START_PATTERN.finditer(question_area))
    blocks: list[tuple[str, str, list[str]]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(question_area)
        block = question_area[match.start():end].strip()
        if len(block) < 4:
            continue
        images = MARKDOWN_IMAGE_PATTERN.findall(block)
        blocks.append((match.group("number"), block, images))
    return blocks


def limited_question_sources(source: str) -> list[tuple[str, str, list[str]]]:
    """Bound per-request model cost while retaining a no-number fallback."""
    blocks = split_question_sources(source)[:MAX_QUESTIONS_PER_BATCH]
    return blocks or [("", source, MARKDOWN_IMAGE_PATTERN.findall(source))]


def select_complete_question_source(source: str) -> tuple[str, str, list[str]]:
    """Select one intact question, preferring an illustrated example."""
    blocks = split_question_sources(source)
    if not blocks:
        return "", source.strip(), MARKDOWN_IMAGE_PATTERN.findall(source)
    return next((block for block in blocks if block[2]), blocks[0])


def question_image_paths(asset_dir: Path, references: list[str]) -> list[Path]:
    """Resolve only safe image basenames referenced by one question."""
    available = {
        path.name: path
        for path in asset_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    } if asset_dir.is_dir() else {}
    return [available[Path(reference).name] for reference in references if Path(reference).name in available]


def question_key(batch_id: str, number: str, index: int) -> str:
    """Build a stable key from a batch and OCR-visible question number."""
    normalized = re.sub(r"[^0-9A-Za-z_-]+", "", number) or f"index-{index + 1:03d}"
    return f"{batch_id}-q-{normalized}"
