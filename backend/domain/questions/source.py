"""Pure helpers for turning OCR Markdown into bounded question sources."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ocr_pipeline import PAGE_MARKER, has_visual_hint

# 只把版心左侧的题号视为新题。小问 ``(1)``、章节编号 ``1.1`` 都可能以数字
# 开头，但前者属于当前题、后者通常不是可独立出题的题号，不能用宽泛的数字正则切开。
QUESTION_START_PATTERN = re.compile(
    r"(?m)^\s*(?:[【\[]\s*)?(?:第\s*)?(?P<number>\d{1,3})(?:(?:\s*题\s*(?:[:：]|\s))|[.．、]|[】\]])\s*"
)
MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
MAX_QUESTIONS_PER_BATCH = 5

ANSWER_SECTION_PATTERN = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:参考答案与解析|答案与解析|参考答案|答案|解答|解析)\s*$"
)
INLINE_ANSWER_PATTERN = re.compile(
    r"(?im)^\s*(?:【\s*)?(?:参考)?(?:答案|解析)\s*(?:】\s*)?"
)


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
    """Split OCR Markdown into bounded questions without answer-key leakage.

    OCR 页之间可能重复打印同一个题号；相邻的同号块按续题合并，既能保住跨页小问，也不把
    下一题吸进来。图片始终随其所在题块保留，顺序与 OCR Markdown 一致。
    """
    question_area = ANSWER_SECTION_PATTERN.split(source, maxsplit=1)[0]
    matches = list(QUESTION_START_PATTERN.finditer(question_area))
    page_markers = list(PAGE_MARKER.finditer(question_area))
    blocks: list[tuple[str, str, list[str]]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(question_area)
        block = INLINE_ANSWER_PATTERN.split(question_area[match.start():end], maxsplit=1)[0].strip()
        if len(block) < 4:
            continue
        images = MARKDOWN_IMAGE_PATTERN.findall(block)
        # 矢量 PDF 图形不会出现在 Markdown 图片列表中。OCR 编排器会在对应页段
        # 注入页面渲染图。题目跨页时仍以题号所在页为准；否则会把下一页的整页图
        # 错绑给上一页末尾的题目。
        if not images and has_visual_hint(block) and page_markers:
            page_marker = next(
                (marker for marker in reversed(page_markers) if marker.start() < match.start()),
                None,
            )
            if page_marker:
                section_end = next(
                    (marker.start() for marker in page_markers if marker.start() > page_marker.start()),
                    len(question_area),
                )
                images = MARKDOWN_IMAGE_PATTERN.findall(question_area[page_marker.end():section_end])
        number = match.group("number")
        if blocks and blocks[-1][0] == number:
            # 同号重复通常来自跨页页眉或 OCR 将续题重新识别为题首。合并而非新建题，
            # 并按出现顺序去重图片，避免同一资源被审核成两次归属。
            previous_number, previous_block, previous_images = blocks[-1]
            merged_images = list(dict.fromkeys([*previous_images, *images]))
            blocks[-1] = (previous_number, f"{previous_block}\n{block}", merged_images)
        else:
            blocks.append((number, block, images))
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
