"""Deterministic source utilities for source-versioned chapter lessons."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ocr_pipeline import PAGE_MARKER


def fingerprint(source: dict[str, Any]) -> str:
    """Return a stable hash for normalized, JSON-compatible source content."""
    encoded = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def sentences(text: str, page: int, revision_id: str) -> list[dict[str, str]]:
    """Build stable sentence evidence records within the public page contract.

    ChapterPage permits at most 200 sentence records and 20,000 characters of
    page text. Rejecting an over-segmented page keeps OCR-derived evidence in
    bounds instead of silently dropping or combining sentence identity.
    """
    if len(text) > 20_000:
        raise ValueError("来源页文本不能超过 20000 个字符")
    spans: list[tuple[int, int]] = []
    start = 0
    for boundary in re.finditer(r"(?<=[.!?。！？；;])\s*|\n+", text):
        if text[start:boundary.start()].strip():
            spans.append((start, boundary.start()))
        start = boundary.end()
    if text[start:].strip():
        spans.append((start, len(text)))

    if len(spans) > 200:
        raise ValueError("来源页最多支持 200 条句子证据")

    return [
        {
            "sentenceId": f"{revision_id[:8]}-p{page}-s{index}",
            "text": text[left:right].strip(),
        }
        for index, (left, right) in enumerate(spans, start=1)
    ]


def pages_from_ocr(text: str) -> list[dict[str, Any]]:
    """Split persisted OCR text at the repository's existing page markers."""
    markers = list(PAGE_MARKER.finditer(text))
    return [
        {
            "page": int(marker.group(1)),
            "text": text[
                marker.end():markers[index + 1].start()
                if index + 1 < len(markers) else len(text)
            ].strip(),
        }
        for index, marker in enumerate(markers)
    ]
