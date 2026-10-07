"""Conservative document routing and chapter boundaries, without inventing headings."""

from __future__ import annotations

import re
from typing import Any

CHAPTER_HEADING = re.compile(r"^(?P<label>第[一二三四五六七八九十百零〇0-9]+(?:章|节|单元)|(?:unit|chapter|module)\s*\d+)[^\n]{0,120}$", re.I)
PAPER_SIGNAL = re.compile(r"试卷|考试|测试卷|答题卡|满分\s*[:：]?\s*\d|考试时间|姓名.{0,12}班级", re.I)
BOOK_SIGNAL = re.compile(r"教材|教程|教科书|课本|tutorial|textbook|student.?s?\s+book|目录|contents|ISBN", re.I)


def headings(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ignore tables of contents and repeated page headers; retain physical page numbers."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in sorted(pages, key=lambda p: p["page"]):
        candidates = [line.strip().lstrip("# ").strip() for line in str(page["text"]).splitlines()[:12]]
        matches = [line for line in candidates if CHAPTER_HEADING.fullmatch(line)]
        if len(matches) > 1 or any(line.strip().lower() in {"目录", "contents", "table of contents"} for line in candidates):
            continue
        for title in matches:
            match = CHAPTER_HEADING.fullmatch(title)
            assert match is not None
            identity = re.sub(r"\s+", "", match.group("label")).lower()
            if identity not in seen:
                found.append({"title": title, "page": int(page["page"])})
                seen.add(identity)
    return found


def classify_material(filename: str, text: str) -> dict[str, str]:
    """Prefer explicit exam evidence over incidental textbook exercise headings."""
    sample = text[:40_000]
    if PAPER_SIGNAL.search(sample) or PAPER_SIGNAL.search(filename) or re.search(r"中考|高考|真题|模拟卷", filename) or re.search(r"\b(?:exam|test paper|question paper)\b", filename, re.I):
        return {"kind": "paper", "reason": "识别到试卷标题或考试信息"}
    if BOOK_SIGNAL.search(sample) or BOOK_SIGNAL.search(filename) or headings([{"page": 1, "text": sample}]):
        return {"kind": "textbook", "reason": "识别到教材、目录或章节标题"}
    if re.search(r"(?:^|\n)\s*\d+[.、．)]\s*\S", sample):
        return {"kind": "paper", "reason": "识别到编号题目"}
    return {"kind": "unknown", "reason": "类型依据不足，保留原文并按课程来源处理"}


def chapter_ranges(starts: list[dict[str, Any]], last_page: int, fallback_title: str) -> list[dict[str, Any]]:
    """Hard-cap creation at five actual chapters, with an explicit bounded fallback."""
    starts = [item for item in starts if 1 <= int(item["page"]) <= last_page]
    if not starts:
        return [{"title": fallback_title[:200], "pageStart": 1, "pageEnd": min(last_page, 80), "boundaryDetected": False}]
    ordered = sorted({int(item["page"]): item for item in starts if 1 <= int(item["page"]) <= last_page}.values(), key=lambda p: p["page"])
    return [
        {"title": item["title"][:200], "pageStart": item["page"],
         "pageEnd": min(ordered[index + 1]["page"] - 1 if index + 1 < len(ordered) else last_page, item["page"] + 79),
         "boundaryDetected": True}
        for index, item in enumerate(ordered[:5])
    ]


def material_preview(job: dict[str, Any], source: str, ocr_run: dict[str, Any], detection: dict[str, str], page_count: int) -> dict[str, Any]:
    """Persist OCR-only material without a fabricated question or model invocation."""
    return {
        "uploadId": job["uploadId"], "importId": job["importId"], "filename": job["filename"],
        "contentType": job["contentType"], "size": job["size"], "stored": True,
        "materialKind": detection["kind"], "detectionReason": detection["reason"],
        "ocrRun": ocr_run, "questionPayloads": [],
        "stages": [{"id": "ocr", "label": "识别文件类型与教材原文", "status": "done"}],
        "extraction": {"chapter": "教材课程", "knowledgePoint": "自动识别章节", "questionCount": 0,
                       "formulaCount": 0, "guideCardCount": 0, "pageCount": page_count,
                       "confidence": 0, "mode": "source-only"},
    }
