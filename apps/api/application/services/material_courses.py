"""Bounded upload-to-course orchestration using existing OCR, source and job contracts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from application.job_worker import JobCancelled
from application.services.chapter_source_preview import file_hash, source_file
from domain.chapters.material import CHAPTER_HEADING, chapter_ranges, headings
from domain.chapters.source import pages_from_ocr
from textbook_ocr_pipeline import resolve_routed_ocr_source

SCAN_PAGE_LIMIT = 80


class MaterialCourseService:
    """Create at most five chapters, retaining source identity and human review gates."""

    def __init__(self, chapters: Any, ocr: Any) -> None:
        self.chapters = chapters
        self.store = chapters.store
        self.jobs = chapters.jobs
        self.ocr = ocr

    def enqueue(self, upload_id: str) -> dict[str, Any]:
        job = self.store.load_job(upload_id)
        if not job or job.get("status") != "complete":
            raise ValueError("请等待文件识别完成")
        if self.jobs is None:
            raise ValueError("课程生成队列尚未就绪")
        from infrastructure.runtime.job_snapshot import current_job_runtime_snapshot
        snapshot = current_job_runtime_snapshot()
        snapshot["ocr"] = {"provider": "auto"}
        return self.jobs.create_job(
            "material.courses.create",
            {"uploadId": upload_id, "runtimeSnapshot": snapshot},
            idempotency_key=f"material:{upload_id}:first-five-tutorial-v2", max_attempts=1,
        )

    @staticmethod
    def _outline(reader: PdfReader) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        def walk(entries: list[Any]) -> None:
            for entry in entries:
                if isinstance(entry, list):
                    walk(entry)
                elif CHAPTER_HEADING.fullmatch(str(entry.get("/Title", "")).strip()):
                    number = reader.get_destination_page_number(entry)
                    if number is not None:
                        found.append({"title": str(entry["/Title"]), "page": number + 1})
        walk(reader.outline)
        # Books may nest actual chapters under large units; prefer the chapter level.
        chapters = [item for item in found if re.match(r"^(?:chapter\s*\d|第[一二三四五六七八九十百零〇0-9]+章)", item["title"], re.I)]
        units = [item for item in found if re.match(r"^(?:unit|module)\s*\d|^第[一二三四五六七八九十百零〇0-9]+单元", item["title"], re.I)]
        selected = chapters or units or found
        return headings([{"page": item["page"], "text": item["title"]} for item in selected])

    def run(self, upload_id: str, cancellation_check: Any) -> dict[str, Any]:
        job = self.store.load_job(upload_id)
        if not job or job.get("status") != "complete":
            raise ValueError("教材来源尚未完成识别")
        path = source_file(self.store, job)
        fingerprint = file_hash(path) if path else ""
        pages = {int(p["page"]): p for p in pages_from_ocr(str(job.get("sourceText") or ""))}
        notices: list[str] = []
        def report(message: str, progress: int) -> None:
            current = self.jobs.latest_for_payload("material.courses.create", "uploadId", upload_id) if self.jobs else None
            if current and current["status"] == "running":
                self.jobs.update_progress(current["jobId"], progress=progress, message=message, worker_id=current["leaseOwner"])

        if path:
            reader = PdfReader(path)
            total = len(reader.pages)
            try:
                starts = self._outline(reader)
            except Exception:
                starts = []
                notices.append("PDF 目录无法解析，改用逐页标题识别")
            if not starts:
                # Scan physical pages in order; stop at the sixth boundary, never invent five chunks.
                for page in range(1, min(total, SCAN_PAGE_LIMIT) + 1):
                    report(f"正在识别章节目录：第 {page} 页（最多扫描 {min(total, SCAN_PAGE_LIMIT)} 页）", 20)
                    self._read_page(job, path, fingerprint, page, pages, cancellation_check, reader)
                    starts = headings(list(pages.values()))
                    if len(starts) >= 6:
                        break
                if total > SCAN_PAGE_LIMIT and len(starts) < 6:
                    notices.append("未找到完整章节边界，已限制扫描前 80 页；课程页段需复核")
                last_page = min(total, max(pages, default=1))
            else:
                last_page = total
            ranges = chapter_ranges(starts, last_page, Path(job["filename"]).stem)
            for index, chapter in enumerate(ranges, start=1):
                for page in range(chapter["pageStart"], chapter["pageEnd"] + 1):
                    report(f"正在读取第 {index}/{len(ranges)} 章：第 {page} 页（本章 {chapter['pageStart']}–{chapter['pageEnd']} 页）", 50)
                    self._read_page(job, path, fingerprint, page, pages, cancellation_check, reader)
            if file_hash(path) != fingerprint:
                raise ValueError("教材原文件发生变化，请重新上传")
        else:
            if not pages:
                raise ValueError("来源缺少逐页 OCR，无法自动制作课程")
            ranges = chapter_ranges(headings(list(pages.values())), max(pages), Path(job["filename"]).stem)
        if not all(item["boundaryDetected"] for item in ranges):
            notices.append("未发现可靠章节标题，先将已有页段制作成一章，未虚构章节")
        if any(item["pageEnd"] - item["pageStart"] + 1 == 80 for item in ranges):
            notices.append("单章最多读取 80 页；达到上限的章节页段请复核")
        selected = {page for chapter in ranges for page in range(chapter["pageStart"], chapter["pageEnd"] + 1)}
        if not all(pages.get(page, {}).get("text", "").strip() or "blank" in pages.get(page, {}).get("flags", []) for page in selected):
            raise ValueError("所选章节有页面未识别，原文件已保留，请重试识别")
        blank_pages = sorted(page for page in selected if "blank" in pages[page].get("flags", []))
        if blank_pages:
            notices.append("已保留经预检确认的空白页：" + "、".join(map(str, blank_pages)))
        # Source text remains a page-addressed snapshot; existing published revisions are untouched.
        existing = {int(p["page"]): p for p in pages_from_ocr(str(job.get("sourceText") or ""))}
        existing.update(pages)
        job["sourceText"] = "\n\n".join(f"<!-- page {page} -->\n{existing[page]['text']}" for page in sorted(existing))
        if any(item["boundaryDetected"] for item in ranges):
            job["result"] = {**(job.get("result") or {}), "materialKind": "textbook", "detectionReason": "识别到教材章节目录"}
        self.store.save_job(job)
        report("章节来源已保存，正在安排课程草稿", 85)
        sample = job["filename"] + " " + " ".join(pages[p]["text"] for p in sorted(selected))[:10_000]
        clean_sample = re.sub(r"!\[[^\]]*\]\([^)]*\)|https?://\S+", "", sample)
        latin = len(re.findall(r"[A-Za-z]", clean_sample))
        chinese = len(re.findall(r"[\u4e00-\u9fff]", clean_sample))
        subject = "math" if re.search(r"数学|math|algebra|geometry|equation|函数|方程|几何", sample, re.I) else (
            "english" if re.search(r"英语|english|(?:unit|chapter|module)\s*\d", sample, re.I) or latin > max(20, chinese * 2) else "math")
        results = []
        for item in ranges[:5]:
            if cancellation_check():
                raise JobCancelled()
            with self.store.chapter_lock(f"auto:{upload_id}"):
                chapter = next((c for c in self.store.list_chapters()
                                if not c.get("deletedAt") and c.get("teachingMode") == "tutorial" and c["title"] == item["title"] and c["sourceRevisions"][0].get("uploadId") == upload_id
                                and c["sourceRevisions"][0].get("pageStart") == item["pageStart"]
                                and c["sourceRevisions"][0].get("pageEnd") == item["pageEnd"]), None)
                if chapter is None:
                    chapter = self.chapters.create({"title": item["title"], "subject": subject, "teachingMode": "tutorial", "source": {
                        "uploadId": upload_id, "sourceVersion": fingerprint or "1",
                        "pageStart": item["pageStart"], "pageEnd": item["pageEnd"], "license": None,
                    }})
            if not chapter.get("currentLessonIds"):
                self.chapters.enqueue_ai_generation(chapter["chapterId"], expected_record_version=chapter["recordVersion"])
            results.append({"chapterId": chapter["chapterId"], "title": chapter["title"], **item})
        return {"chapters": results, "chapterLimit": 5, "notices": notices, "requiresHumanReview": True}

    def _read_page(
        self, job: dict[str, Any], path: Path, fingerprint: str, page: int,
        pages: dict[int, dict[str, Any]], cancellation_check: Any, reader: PdfReader | None = None,
    ) -> None:
        if cancellation_check():
            raise JobCancelled()
        if pages.get(page, {}).get("text", "").strip():
            return
        text, audit = resolve_routed_ocr_source(
            runtime=self.ocr, source_text="", source_path=path, reader=reader, start_page=page - 1, end_page=page - 1,
            asset_dir=Path(job["directory"]) / "assets" / f"chapter-page-{page}",
            asset_url_prefix=f"/api/uploads/{job['uploadId']}/assets/chapter-page-{page}",
            cache_dir=Path(job["directory"]) / "ocr-cache", content_hash=fingerprint,
        )
        resolved = pages_from_ocr(text)
        pages[page] = next((p for p in resolved if p["page"] == page), {"page": page, "text": text.strip()})
        if not pages[page]["text"].strip() and any(
            route.get("preflight", {}).get("category") == "blank" for route in audit.get("pageRoutes", [])
        ):
            pages[page]["flags"] = ["blank"]
        # Keep completed OCR across a later failure or cancellation, avoiding paid rereads.
        job["sourceText"] = "\n\n".join(f"<!-- page {number} -->\n{pages[number]['text']}" for number in sorted(pages))
        self.store.save_job(job)
