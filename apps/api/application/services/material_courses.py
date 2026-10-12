"""Prepare an explicitly classified textbook source for courseware authoring.

This path snapshots existing OCR and creates one source-only tutorial chapter.
It never infers a subject, splits a book into five lessons, or starts the legacy
four-markdown lesson generator.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from application.job_worker import (
    CancellableJobResult,
    JobCancelled,
    JobExecutionContext,
)
from application.services.chapter_source_preview import file_hash, source_file
from domain.chapters.material import classify_material
from domain.chapters.source import pages_from_ocr
from domain.courseware.profiles import CoursewareProfileRegistry, courseware_profiles


class MaterialCourseService:
    """Queue a source preparation job that yields a reviewable tutorial chapter."""

    JOB_TYPE = "courseware.source.prepare"

    def __init__(self, chapters: Any, ocr: Any | None = None, *, profiles: CoursewareProfileRegistry = courseware_profiles) -> None:
        self.chapters = chapters
        self.store = chapters.store
        self.jobs = chapters.jobs
        self.profiles = profiles

    def enqueue(
        self,
        upload_id: str,
        request: Any,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Validate classification and enqueue a frozen source selection."""
        upload = self.store.load_job(upload_id)
        if not upload or upload.get("status") not in {"complete", "completed", "ready"}:
            raise ValueError("请等待文件识别完成")
        if self.jobs is None:
            raise ValueError("课件来源准备队列尚未就绪")

        result = upload.get("result") if isinstance(upload.get("result"), dict) else {}
        detected = str(result.get("materialKind") or classify_material(
            str(upload.get("filename") or ""), str(upload.get("sourceText") or ""),
        )["kind"])
        if detected in {"paper", "exam"}:
            raise ValueError("试卷必须保留原题生产路径，不能转成教材课件")
        if detected != "textbook" and request.confirmedMaterialKind != "textbook":
            raise ValueError("材料类型尚未确认；请先明确选择教材")
        profile = self.profiles.get(request.subject)

        pages = pages_from_ocr(str(upload.get("sourceText") or ""))
        if not pages:
            raise ValueError("上传材料没有可复用的逐页 OCR 来源")
        start = int(request.pageStart if request.pageStart is not None else min(int(p["page"]) for p in pages))
        end = int(request.pageEnd if request.pageEnd is not None else max(int(p["page"]) for p in pages))
        if end < start or end - start + 1 > 80:
            raise ValueError("请明确选择不超过 80 页的教材来源范围")
        by_page = {int(page["page"]): page for page in pages}
        missing = sorted(set(range(start, end + 1)) - set(by_page))
        if missing:
            raise ValueError("教材来源缺少所选页：" + ", ".join(map(str, missing[:12])))
        selected = [by_page[number] for number in range(start, end + 1)]
        if any(not str(page.get("text") or "").strip() for page in selected):
            raise ValueError("所选页包含未识别页面，请先完成 OCR")

        path = source_file(self.store, upload)
        source_file_sha256 = file_hash(path) if path else None
        source_text_sha256 = hashlib.sha256(str(upload.get("sourceText") or "").encode()).hexdigest()
        request_data = request.model_dump(mode="json")
        request_hash = hashlib.sha256(
            f"{upload_id}:{source_text_sha256}:{source_file_sha256}:{request.model_dump_json()}".encode(),
        ).hexdigest()
        caller_key = (idempotency_key or request_hash).strip()[:128]
        key = f"courseware-source:{upload_id}:{caller_key}"
        payload = {
            "uploadId": upload_id,
            "filename": str(upload.get("filename") or "教材"),
            "materialKind": "textbook",
            "detectedMaterialKind": detected,
            "request": request_data,
            "pageStart": start,
            "pageEnd": end,
            "sourceTextSha256": source_text_sha256,
            "sourceFileSha256": source_file_sha256,
            "requestHash": request_hash,
            "title": request.title or Path(str(upload.get("filename") or "教材")).stem[:200],
            "subject": profile.subject_id,
        }
        return self.jobs.create_job(
            self.JOB_TYPE,
            payload,
            idempotency_key=key,
            max_attempts=3,
            reject_payload_mismatch=True,
        )

    def run(
        self,
        payload: dict[str, Any],
        cancellation_check: Any,
        context: JobExecutionContext,
    ) -> CancellableJobResult:
        """Create one source-only chapter under a lease and source fingerprint guard."""
        if not self.jobs.execution_is_current(context.job_id, context.worker_id, context.attempt_count):
            raise ValueError("教材来源准备租约已过期或已被接管")
        if cancellation_check():
            raise JobCancelled()
        upload = self.store.load_job(str(payload["uploadId"]))
        if not upload or upload.get("status") not in {"complete", "completed", "ready"}:
            raise ValueError("教材来源在准备期间已不可用")
        current_text_hash = hashlib.sha256(str(upload.get("sourceText") or "").encode()).hexdigest()
        if current_text_hash != payload["sourceTextSha256"]:
            raise ValueError("教材 OCR 来源在准备期间已变化")
        path = source_file(self.store, upload)
        current_file_hash = file_hash(path) if path else None
        if current_file_hash != payload.get("sourceFileSha256"):
            raise ValueError("教材原文件在准备期间已变化")

        # A retry after the chapter transaction committed returns that exact result.
        existing = next((chapter for chapter in self.store.list_chapters()
                         if chapter.get("sourcePreparationJobId") == context.job_id
                         and not chapter.get("deletedAt")), None)
        if existing:
            source = (existing.get("sourceRevisions") or [{}])[-1]
            if source.get("sourceFileSha256") != payload.get("sourceFileSha256"):
                raise ValueError("已准备来源与当前文件版本不一致")
            return CancellableJobResult(self._result(existing, source, payload))

        request = payload["request"]
        pages = pages_from_ocr(str(upload.get("sourceText") or ""))
        by_page = {int(page["page"]): page for page in pages}
        start, end = int(payload["pageStart"]), int(payload["pageEnd"])
        selected_pages = [by_page[number] for number in range(start, end + 1)]
        chapter_request = {
            "title": payload["title"],
            "subject": payload["subject"],
            "teachingMode": "tutorial",
            "sourcePreparationJobId": context.job_id,
            "source": {
                "uploadId": payload["uploadId"],
                "sourceVersion": str(payload.get("sourceFileSha256") or payload["sourceTextSha256"]),
                "sourceTitle": request.get("sourceTitle") or payload["title"],
                "sourceUrl": request.get("sourceUrl"),
                "license": request.get("license"),
                "attributionText": request.get("attributionText"),
                "distributionScope": request.get("distributionScope", "private"),
                "pageStart": start,
                "pageEnd": end,
                "pages": selected_pages,
            },
        }
        created = self.chapters.create(
            chapter_request,
            execution_context=context,
            source_guard={
                "uploadId": payload["uploadId"],
                "sourceTextSha256": payload["sourceTextSha256"],
            },
        )
        source = created["sourceRevisions"][-1]

        def rollback_cancelled() -> None:
            with self.store.chapter_lock(created["chapterId"]):
                with self.store.atomic():
                    latest = self.store.load_chapter(created["chapterId"])
                    if (not latest or latest.get("sourcePreparationJobId") != context.job_id
                            or int(latest.get("recordVersion", 0)) != int(created["recordVersion"])
                            or latest.get("lessons")):
                        return
                    latest["deletedAt"] = time.time()
                    self.store.save_chapter(latest)

        return CancellableJobResult(self._result(created, source, payload), on_cancel=rollback_cancelled)

    @staticmethod
    def _result(chapter: dict[str, Any], source: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "materialKind": "textbook",
            "subject": payload["subject"],
            "chapters": [{
                "chapterId": chapter["chapterId"],
                "title": chapter["title"],
                "subject": chapter["subject"],
                "sourceRevisionId": source["sourceRevisionId"],
                "recordVersion": chapter["recordVersion"],
                "pageStart": source["pageStart"],
                "pageEnd": source["pageEnd"],
            }],
            "notices": [],
            "requiresHumanReview": True,
        }
