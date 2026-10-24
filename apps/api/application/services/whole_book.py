"""Durable, bounded orchestration for explicitly selected textbook books.

The run itself and its batches live in the existing background-job table. A
controller job only schedules or observes one batch; source, planning, page,
review, repair, and publication work stay in the existing courseware services.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from application.job_worker import JobCancelled, JobExecutionContext, RetryableJobError
from application.services.chapter_source_preview import file_hash, source_file
from domain.chapters.material import classify_material, headings
from domain.chapters.source import pages_from_upload
from domain.contracts.courseware import CoursewareSourcePreparationRequest
from domain.contracts.whole_book import (
    MAX_GENERATED_PAGES_PER_BATCH,
    MAX_WHOLE_BOOK_BATCH_PAGES,
    MAX_WHOLE_BOOK_BATCHES,
    MAX_WHOLE_BOOK_PAGES,
    WholeBookBatchView,
    WholeBookRunView,
    WholeBookStartRequest,
)
from infrastructure.runtime.job_snapshot import (
    current_job_runtime_snapshot,
    use_job_runtime_snapshot,
)

# More than one teaching page may be needed for a physical page. This independent
# ceiling keeps model and reviewer calls bounded at 45 authored pages per batch.
START_JOB_TYPE = "courseware.whole_book.start"
ADVANCE_JOB_TYPE = "courseware.whole_book.advance"
CONTROL_JOB_TYPE = "courseware.whole_book.control"


def whole_book_batches(
    page_count: int,
    pages: list[dict[str, Any]],
    book_title: str,
    *,
    max_pages: int = MAX_WHOLE_BOOK_BATCH_PAGES,
) -> list[dict[str, Any]]:
    """Cover every physical page once, preferring OCR chapter boundaries."""
    if type(page_count) is not int or not 1 <= page_count <= MAX_WHOLE_BOOK_PAGES:
        raise ValueError(f"整本自动制作目前支持 1–{MAX_WHOLE_BOOK_PAGES} 页教材")
    if type(max_pages) is not int or not 1 <= max_pages <= MAX_WHOLE_BOOK_BATCH_PAGES:
        raise ValueError(f"每批最多 {MAX_WHOLE_BOOK_BATCH_PAGES} 个原PDF物理页")
    chapter_starts = {
        int(item["page"]): str(item["title"]).strip()
        for item in headings(pages)
        if 1 <= int(item["page"]) <= page_count
    }
    starts = sorted(chapter_starts)
    target_batch_count = (page_count + max_pages - 1) // max_pages
    batches: list[dict[str, Any]] = []
    page_start = 1
    while page_start <= page_count:
        max_end = min(page_count, page_start + max_pages - 1)
        # A chapter start belongs to the following batch. Use a boundary only
        # when it does not increase the minimum number of batches required for
        # the entire book; dense chapter headings must never exceed the ceiling.
        remaining_pages = page_count - page_start + 1
        remaining_batches = (remaining_pages + max_pages - 1) // max_pages
        minimum_batch_length = remaining_pages - (remaining_batches - 1) * max_pages
        minimum_end = page_start + minimum_batch_length - 1
        boundaries = [
            number - 1 for number in starts
            if minimum_end <= number - 1 < max_end
        ]
        page_end = max(boundaries) if boundaries else max_end
        previous_starts = [number for number in starts if number <= page_start]
        section = chapter_starts[max(previous_starts)] if previous_starts else None
        if page_start in chapter_starts:
            section = chapter_starts[page_start]
        if section:
            title = f"{book_title} · {section}（第 {page_start}–{page_end} 页）"
        else:
            title = f"{book_title}（第 {page_start}–{page_end} 页）"
        batches.append({
            "batchIndex": len(batches),
            "title": title[:200],
            "pageStart": page_start,
            "pageEnd": page_end,
            "chapterHeading": section,
        })
        page_start = page_end + 1
    if len(batches) > min(target_batch_count, MAX_WHOLE_BOOK_BATCHES):
        raise ValueError(f"整本教材最多建立 {MAX_WHOLE_BOOK_BATCHES} 个制作批次")
    return batches


class WholeBookService:
    """Start, inspect, pause, resume, and advance a complete-book run."""

    def __init__(
        self,
        *,
        store: Any,
        jobs: Any,
        material_courses: Any,
        courseware: Any,
    ) -> None:
        self.store = store
        self.jobs = jobs
        self.material_courses = material_courses
        self.courseware = courseware

    def _upload_fingerprints(self, upload_id: str) -> tuple[dict[str, Any], Path, str, str, int]:
        upload = self.store.load_job(upload_id)
        if not upload or upload.get("status") not in {"complete", "completed", "ready"}:
            raise LookupError("教材文件尚未完成识别")
        path = source_file(self.store, upload)
        if path is None:
            raise LookupError("教材原PDF暂不可用，无法启动整本制作")
        try:
            page_count = len(PdfReader(path).pages)
        except Exception as error:
            raise ValueError("教材原PDF无法读取，请重新上传有效的PDF文件") from error
        if not 1 <= page_count <= MAX_WHOLE_BOOK_PAGES:
            raise ValueError(f"整本自动制作目前支持 1–{MAX_WHOLE_BOOK_PAGES} 页教材")
        return (
            upload,
            path,
            file_hash(path),
            hashlib.sha256(str(upload.get("sourceText") or "").encode("utf-8")).hexdigest(),
            page_count,
        )

    @staticmethod
    def _source_identity(upload_id: str, source_file_sha256: str, source_text_sha256: str) -> str:
        identity = f"{upload_id}:{source_file_sha256}:{source_text_sha256}"
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def _start_key(self, upload_id: str, source_file_sha256: str, source_text_sha256: str) -> str:
        return f"courseware-whole-book:{self._source_identity(upload_id, source_file_sha256, source_text_sha256)}"

    def _batch_key(self, payload: dict[str, Any], batch: dict[str, Any]) -> str:
        identity = self._source_identity(
            str(payload["uploadId"]), str(payload["sourceFileSha256"]), str(payload["sourceTextSha256"]),
        )
        return f"wholebook:{identity[:32]}:batch:{int(batch['batchIndex'])}"

    def _source_job_key(self, payload: dict[str, Any], batch: dict[str, Any]) -> str:
        return f"courseware-source:{payload['uploadId']}:{self._batch_key(payload, batch)}"

    def start(
        self,
        upload_id: str,
        request: WholeBookStartRequest,
        *,
        actor_id: str,
    ) -> WholeBookRunView:
        """Persist an explicit whole-book intent; repeated calls reuse its run."""
        upload, path, source_file_sha256, source_text_sha256, page_count = self._upload_fingerprints(upload_id)
        result_value = upload.get("result")
        result = result_value if isinstance(result_value, dict) else {}
        detected = str(result.get("materialKind") or classify_material(
            str(upload.get("filename") or ""), str(upload.get("sourceText") or ""),
        )["kind"])
        if detected in {"paper", "exam"}:
            raise ValueError("试卷必须保留原题生产路径，不能转成教材课件")
        if detected != "textbook" and request.confirmedMaterialKind != "textbook":
            raise ValueError("材料类型尚未确认；请先明确选择教材")
        self.courseware.profiles.get(request.subject)

        pages = pages_from_upload(upload)
        filename_title = Path(str(upload.get("filename") or "教材")).stem[:200] or "教材"
        title = request.title or filename_title
        batches = whole_book_batches(page_count, pages, title)
        request_json = request.model_dump(mode="json")
        request_hash = hashlib.sha256(json.dumps(
            request_json, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        run_id = uuid.uuid4().hex
        payload = {
            "runId": run_id,
            "uploadId": upload_id,
            "filename": str(upload.get("filename") or "教材"),
            "materialKind": "textbook",
            "detectedMaterialKind": detected,
            "sourceFileSha256": source_file_sha256,
            "sourceTextSha256": source_text_sha256,
            "sourceFileStat": self._file_stat(path),
            "request": request_json,
            "requestHash": request_hash,
            "pageCount": page_count,
            "batches": batches,
            "runtimeSnapshot": current_job_runtime_snapshot(),
            "requestedBy": actor_id,
            "createdAt": time.time(),
        }
        run = self.jobs.create_job(
            START_JOB_TYPE,
            payload,
            job_id=run_id,
            idempotency_key=self._start_key(upload_id, source_file_sha256, source_text_sha256),
            # Source restoration failures are cheap and retryable; this is a
            # bounded controller budget, not a model-generation attempt count.
            max_attempts=20,
            # The first writer owns createdAt, runtime snapshot, and actor ID.
            # The immutable requestHash below rejects different intent while
            # allowing a duplicate request to recover that original run.
            reject_payload_mismatch=False,
        )
        if run["payload"].get("requestHash") != request_hash:
            raise ValueError("同一教材来源已经有整本制作任务；请继续原任务，或先创建新的教材来源修订")
        return self.get_run(str(run["jobId"]))

    @staticmethod
    def _file_stat(path: Path) -> dict[str, int]:
        stat = path.stat()
        return {"size": int(stat.st_size), "mtimeNs": int(stat.st_mtime_ns), "inode": int(stat.st_ino)}

    def latest_for_upload(self, upload_id: str) -> WholeBookRunView | None:
        """Read the latest whole-book run without enqueuing or invoking a model."""
        run = self.jobs.latest_for_payload(START_JOB_TYPE, "uploadId", upload_id)
        return self.get_run(str(run["jobId"])) if run else None

    def get_run(self, run_id: str) -> WholeBookRunView:
        run = self.jobs.get_job(run_id)
        if not run or run.get("jobType") != START_JOB_TYPE:
            raise LookupError("整本教材制作任务不存在")
        payload = run["payload"]
        views = [self._batch_progress(payload, batch) for batch in payload["batches"]]
        completed = [item for item in views if item.status in {"published", "completed"}]
        completed_pages = sum(int(item.pageEnd) - int(item.pageStart) + 1 for item in completed)
        current = next((item for item in views if item.status not in {"published", "completed"}), None)
        source_changed, unavailable = self._source_change_state(payload)
        controller = self._latest_controller(run_id)
        waiting_for_source = bool(controller and controller.get("status") in {"queued", "running"})
        if source_changed:
            status = "source_changed"
            message = "教材原文件或OCR内容已变化；旧任务已停止推进，请基于新的教材来源重新开始。"
        elif unavailable:
            status = "waiting_for_source" if waiting_for_source else "needs_attention"
            message = unavailable if waiting_for_source else f"{unavailable}后台重试预算已用尽，请恢复原件后重试。"
        elif run.get("status") in {"failed", "cancelled"}:
            status = "needs_attention"
            root_error = run.get("lastError") or {}
            message = str(root_error.get("message") or "整本任务启动失败，请查看后台任务记录。")
        elif any(item.status in {"failed", "needs_attention", "source_changed"} for item in views):
            status = "needs_attention"
            message = current.message if current else "本批制作遇到异常，请检查失败原因。"
        elif len(completed) == len(views):
            status = "completed"
            message = "整本教材已完成；所有课时已发布或记录了明确的非教学页跳过原因。"
        elif self._is_paused(run_id):
            status = "paused"
            message = "已暂停续做；当前已完成课时可直接使用，继续后会从未完成批次接着制作。"
        elif run.get("status") in {"queued", "running"} and not any(item.status != "queued" for item in views):
            status = "queued"
            message = "整本制作已排队，Worker 会从第一个原页批次开始。"
        else:
            status = "running"
            message = current.message if current else "正在等待下一批教材原页。"
        return WholeBookRunView(
            runId=str(run["jobId"]),
            uploadId=str(payload["uploadId"]),
            filename=str(payload["filename"]),
            status=status,
            message=message,
            totalPages=int(payload["pageCount"]),
            completedPages=completed_pages,
            completedBatches=len(completed),
            batchCount=len(views),
            currentBatchIndex=int(current.batchIndex) if current else None,
            batches=views,
            canPause=status in {"queued", "running"},
            canResume=status == "paused",
            createdAt=float(run.get("createdAt") or payload.get("createdAt") or 0),
            updatedAt=float(run.get("updatedAt") or payload.get("createdAt") or 0),
        )

    def pause(self, run_id: str, *, command_id: str, actor_id: str) -> WholeBookRunView:
        run = self._require_run(run_id)
        if self._existing_control(run, "pause", command_id):
            return self.get_run(run_id)
        view = self.get_run(run_id)
        if view.status not in {"queued", "running"}:
            raise ValueError("当前整本任务不能暂停")
        self._record_control(run, "pause", command_id=command_id, actor_id=actor_id)
        return self.get_run(run_id)

    def resume(self, run_id: str, *, command_id: str, actor_id: str) -> WholeBookRunView:
        run = self._require_run(run_id)
        if self._existing_control(run, "resume", command_id):
            return self.get_run(run_id)
        view = self.get_run(run_id)
        if view.status != "paused":
            raise ValueError("只有已暂停的整本任务可以继续")
        self._record_control(run, "resume", command_id=command_id, actor_id=actor_id)
        batch_index = view.currentBatchIndex
        self.jobs.create_job(
            ADVANCE_JOB_TYPE,
            {"runId": run_id, "batchIndex": batch_index, "commandId": command_id},
            idempotency_key=f"whole-book-advance:{run_id}:resume:{command_id}"[:255],
            max_attempts=100,
            reject_payload_mismatch=True,
        )
        return self.get_run(run_id)

    def _existing_control(self, run: dict[str, Any], action: str, command_id: str) -> dict[str, Any] | None:
        key = f"whole-book-control:{run['jobId']}:{action}:{command_id}"[:255]
        existing = self.jobs.get_by_idempotency_key(key)
        if existing is None:
            return None
        payload = existing.get("payload") if isinstance(existing.get("payload"), dict) else {}
        if (str(payload.get("runId")) != str(run["jobId"])
                or payload.get("action") != action
                or payload.get("commandId") != command_id):
            raise ValueError("整本任务控制命令与已有命令不一致")
        return existing

    def _record_control(self, run: dict[str, Any], action: str, *, command_id: str, actor_id: str) -> None:
        if not command_id.strip() or len(command_id) > 128:
            raise ValueError("控制命令 ID 无效")
        self.jobs.create_job(
            CONTROL_JOB_TYPE,
            {"runId": str(run["jobId"]), "action": action, "commandId": command_id, "requestedBy": actor_id},
            idempotency_key=f"whole-book-control:{run['jobId']}:{action}:{command_id}"[:255],
            max_attempts=1,
            reject_payload_mismatch=True,
        )

    def _require_run(self, run_id: str) -> dict[str, Any]:
        run = self.jobs.get_job(run_id)
        if not run or run.get("jobType") != START_JOB_TYPE:
            raise LookupError("整本教材制作任务不存在")
        return run

    def _is_paused(self, run_id: str) -> bool:
        latest = self.jobs.latest_for_payload(CONTROL_JOB_TYPE, "runId", run_id)
        return bool(latest and (latest.get("payload") or {}).get("action") == "pause")

    def _latest_controller(self, run_id: str) -> dict[str, Any] | None:
        candidates = [
            self.jobs.get_job(run_id),
            self.jobs.latest_for_payload(ADVANCE_JOB_TYPE, "runId", run_id),
        ]
        live = [
            item for item in candidates
            if item and item.get("status") in {"queued", "running"}
        ]
        return max(live, key=lambda item: float(item.get("updatedAt") or 0), default=None)

    def _local_source_file(self, upload: dict[str, Any]) -> Path | None:
        """Resolve the source PDF without triggering remote restore on read-only status requests."""
        try:
            root = Path(self.store.upload_root).resolve()
            directory = Path(upload["directory"]).resolve()
            path = (directory / "source.pdf").resolve()
            if not directory.is_relative_to(root) or path.parent != directory or not path.is_file():
                return None
            return path
        except (KeyError, OSError, TypeError, ValueError):
            return None

    def _source_change_state(
        self, payload: dict[str, Any], *, restore: bool = False,
    ) -> tuple[bool, str | None]:
        try:
            upload = self.store.load_job(str(payload["uploadId"]))
            if not upload:
                return False, "教材文件记录暂不可用，请恢复文件后重试。"
            source_text_hash = hashlib.sha256(str(upload.get("sourceText") or "").encode("utf-8")).hexdigest()
            if source_text_hash != payload["sourceTextSha256"]:
                return True, None
            path = source_file(self.store, upload) if restore else self._local_source_file(upload)
            if path is None:
                file_assets = getattr(self.store, "file_assets", None)
                read_manifest = getattr(file_assets, "get_upload_file_manifest", None)
                manifest = read_manifest(str(payload["uploadId"]), "source.pdf") if callable(read_manifest) else None
                if isinstance(manifest, dict):
                    manifest_sha256 = manifest.get("sha256")
                    if not isinstance(manifest_sha256, str) or manifest_sha256 != str(payload["sourceFileSha256"]):
                        return True, None
                    return False, None
                if manifest is not None:
                    return False, "教材原件清单暂不可用，整本任务等待重新核验。"
                return False, "教材原PDF暂不可用，整本任务等待恢复原件。"
            stat = self._file_stat(path)
            if stat != payload.get("sourceFileStat"):
                return file_hash(path) != payload["sourceFileSha256"], None
            return False, None
        except Exception:
            return False, "无法校验教材原件；整本任务等待原件恢复或人工检查。"

    def _batch_progress(self, run_payload: dict[str, Any], batch: dict[str, Any]) -> WholeBookBatchView:
        batch_index = int(batch["batchIndex"])
        source_job = self.jobs.get_by_idempotency_key(self._source_job_key(run_payload, batch))
        base = {
            "batchIndex": batch_index,
            "title": batch["title"],
            "pageStart": int(batch["pageStart"]),
            "pageEnd": int(batch["pageEnd"]),
            "coursewareIds": [],
            "publicationIds": [],
            "skippedPages": [],
        }
        if source_job is None:
            return WholeBookBatchView(**base, status="queued", message="等待前一批完成后开始准备原页。")
        if source_job["status"] in {"queued", "running"}:
            return WholeBookBatchView(**base, status="source_preparation", message=source_job.get("message") or "正在准备教材原页。")
        if source_job["status"] in {"failed", "cancelled"}:
            error = source_job.get("lastError") or {"code": source_job["status"], "message": source_job.get("message") or "教材来源准备失败"}
            return WholeBookBatchView(**base, status="failed", message=str(error.get("message") or "教材来源准备失败"), error=self._safe_error(error))
        result = source_job.get("result") if isinstance(source_job.get("result"), dict) else {}
        chapters = result.get("chapters") if isinstance(result.get("chapters"), list) else []
        chapter_id = str(chapters[0].get("chapterId")) if chapters and isinstance(chapters[0], dict) else ""
        if not chapter_id:
            return WholeBookBatchView(**base, status="needs_attention", message="原页准备任务已完成，但没有教材章节结果。", error={"code": "missing_chapter", "message": "请检查教材来源任务。"})
        base["chapterId"] = chapter_id
        plan_job_id = str(result.get("planJobId") or "")
        plan = self.courseware.store.load_plan(chapter_id)
        plan_job = self.jobs.get_job(plan_job_id) if plan_job_id else None
        if plan_job and plan_job["status"] in {"queued", "running"}:
            return WholeBookBatchView(**base, status="planning", message=plan_job.get("message") or "正在规划本批教材原页。")
        if plan_job and plan_job["status"] in {"failed", "cancelled"}:
            error = plan_job.get("lastError") or {"code": plan_job["status"], "message": plan_job.get("message") or "教学规划失败"}
            return WholeBookBatchView(**base, status="failed", message=str(error.get("message") or "教学规划失败"), error=self._safe_error(error))
        if plan is None:
            return WholeBookBatchView(**base, status="needs_attention", message="教材来源已准备，但教学规划记录暂不可用。", error={"code": "missing_plan", "message": "请检查本批任务记录。"})
        dispositions = self._plan_dispositions(plan)
        skipped = [item for item in dispositions if item.get("disposition") == "skipped"]
        base["skippedPages"] = [{key: item[key] for key in ("page", "reason", "note") if key in item} for item in skipped]
        if plan.get("generationStatus") in {"queued", "running"}:
            return WholeBookBatchView(**base, status="planning", message="正在规划本批教材原页。")
        if plan.get("status") == "draft" and not plan.get("sessions"):
            if len(skipped) == base["pageEnd"] - base["pageStart"] + 1:
                return WholeBookBatchView(**base, status="completed", message="本批原页均为已记录原因的非教学内容，已计入整本进度。")
            return WholeBookBatchView(**base, status="needs_attention", message="本批没有生成可用课时。", error={"code": "empty_plan", "message": "请检查教材内容与原页识别。"})
        courseware_ids = [str(item) for item in plan.get("coursewareIds", [])]
        if not courseware_ids:
            return WholeBookBatchView(**base, status="needs_attention", message="本批规划没有可用课时。", error={"code": "missing_courseware", "message": "请检查课时规划。"})
        base["coursewareIds"] = courseware_ids
        documents = [self.courseware.store.load_document(item) for item in courseware_ids]
        if any(document is None for document in documents):
            return WholeBookBatchView(**base, status="needs_attention", message="本批课时记录暂不可用。", error={"code": "missing_document", "message": "请检查课件记录。"})
        live_documents = [document for document in documents if document]
        published = [document for document in live_documents if document.get("status") == "published" and document.get("publicationId")]
        base["publicationIds"] = [str(document["publicationId"]) for document in published]
        if len(published) == len(live_documents):
            coverage = self.courseware.get_coverage(chapter_id, verify_source_file=False)
            covered_pages = {
                int(item["page"]) for item in dispositions
                if item.get("disposition") == "covered"
            }
            unpublished = sorted(covered_pages - set(coverage.get("publishedPages", [])))
            if unpublished:
                shown = "、".join(map(str, unpublished[:8]))
                return WholeBookBatchView(**base, status="needs_attention", message=f"已发布课时仍缺少教材第 {shown} 页的内容覆盖，请检查本批课时。", error={"code": "page_coverage_gap", "message": "规划必须覆盖有效教学页，跳过页需带有可核验的原因。"})
            return WholeBookBatchView(**base, status="published", message="本批课时已完成检查并发布，可直接使用。")
        for document in live_documents:
            automation = document.get("automation") or {}
            if automation.get("status") == "needs_attention":
                return WholeBookBatchView(**base, status="needs_attention", message=str(automation.get("message") or "本批课时仍有内容异常需要处理。"), error={"code": "quality_attention", "message": str(automation.get("message") or "自动检查未通过")})
            if automation.get("status") == "paused":
                return WholeBookBatchView(**base, status="needs_attention", message="课时已转为手动编辑；请完成检查和发布后再继续整本制作。", error={"code": "manual_edit", "message": "自动流程保留了人工修改，没有覆盖课时。"})
            if document.get("generationStatus") in {"failed", "partial", "cancelled"}:
                job = self.jobs.get_job(str(document.get("generationJobId") or ""))
                error = (job or {}).get("lastError") or {"code": "page_generation_failed", "message": "课时页面未能完整生成"}
                return WholeBookBatchView(**base, status="failed", message=str(error.get("message") or "课时页面未能完整生成"), error=self._safe_error(error))
            if automation.get("status") in {"checking", "repairing"}:
                status = "checking" if automation.get("status") == "checking" else "repairing"
                return WholeBookBatchView(**base, status=status, message=str(automation.get("message") or "正在自动检查和修复课时。"))
        return WholeBookBatchView(**base, status="generating", message="正在生成本批课时页面。")

    @staticmethod
    def _plan_dispositions(plan: dict[str, Any]) -> list[dict[str, Any]]:
        raw = (plan.get("generationAudit") or {}).get("wholeBookPageDispositions")
        if not isinstance(raw, str):
            return []
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return []
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    @staticmethod
    def _safe_error(error: dict[str, Any]) -> dict[str, str | bool]:
        details_value = error.get("details")
        details: dict[str, Any] = details_value if isinstance(details_value, dict) else {}
        stage = details.get("stage")
        return {
            "code": str(error.get("code") or error.get("type") or "job_failed")[:100],
            "message": str(error.get("message") or "后台任务未能完成")[:800],
            "retryable": bool(error.get("retryable")),
            **({"stage": str(stage)[:80]} if stage else {}),
        }

    def advance(
        self,
        run_id: str,
        cancellation_check: Any,
        context: JobExecutionContext,
    ) -> dict[str, Any]:
        """Advance only after a child task settles; waiting keeps this lease alive."""
        run = self._require_run(run_id)
        if cancellation_check():
            raise JobCancelled()
        run_payload = run["payload"]
        while True:
            if cancellation_check():
                raise JobCancelled()
            if self._is_paused(run_id):
                return {"runId": run_id, "status": "paused"}
            source_changed, unavailable = self._source_change_state(run_payload, restore=True)
            if source_changed:
                return {"runId": run_id, "status": "source_changed"}
            if unavailable:
                raise RetryableJobError(unavailable)
            progress = [self._batch_progress(run_payload, batch) for batch in run_payload["batches"]]
            blocked = next((item for item in progress if item.status in {"failed", "needs_attention", "source_changed"}), None)
            if blocked:
                return {"runId": run_id, "status": "needs_attention", "batchIndex": blocked.batchIndex}
            next_index = next((index for index, item in enumerate(progress) if item.status not in {"published", "completed"}), None)
            if next_index is None:
                return {"runId": run_id, "status": "completed", "completedBatches": len(progress)}
            batch = run_payload["batches"][next_index]
            source_job = self.jobs.get_by_idempotency_key(self._source_job_key(run_payload, batch))
            if source_job is None:
                # Hash the full source at each batch boundary. The existing source
                # worker repeats the guard immediately before OCR and chapter writes.
                matches, message = self._verify_source(run_payload)
                if not matches:
                    return {"runId": run_id, "status": "source_changed", "message": message}
                self._enqueue_batch(run_payload, batch)
                return {"runId": run_id, "status": "running", "batchIndex": next_index}
            if source_job["status"] in {"failed", "cancelled"}:
                return {"runId": run_id, "status": "needs_attention", "batchIndex": next_index}
            if source_job["status"] in {"queued", "running"}:
                active = [source_job] if source_job["status"] == "running" else []
                if not active:
                    return {"runId": run_id, "status": "waiting", "batchIndex": next_index}
                self._wait_for_running(run_id, run_payload, active, cancellation_check)
                continue
            source_result = source_job.get("result") if isinstance(source_job.get("result"), dict) else {}
            plan_job_id = str(source_result.get("planJobId") or "")
            plan_job = self.jobs.get_job(plan_job_id) if plan_job_id else None
            if plan_job and plan_job["status"] in {"queued", "running"}:
                if plan_job["status"] == "queued":
                    return {"runId": run_id, "status": "waiting", "batchIndex": next_index}
                self._wait_for_running(run_id, run_payload, [plan_job], cancellation_check)
                continue
            # Child generation can enqueue the next stage before its own worker
            # commits terminal state. The callback controller waits only while
            # those jobs are actively running; queued work can use this Worker.
            inspected = self._batch_progress(run_payload, batch)
            if inspected.status in {"published", "completed", "failed", "needs_attention"}:
                continue
            active = self._batch_active_jobs(run_payload, batch)
            if active:
                self._wait_for_running(run_id, run_payload, active, cancellation_check)
                continue
            return {"runId": run_id, "status": "waiting", "batchIndex": next_index}

    def _verify_source(self, payload: dict[str, Any]) -> tuple[bool, str]:
        changed, unavailable = self._source_change_state(payload, restore=True)
        if unavailable:
            raise RetryableJobError(unavailable)
        if changed:
            return False, "教材原文件或OCR内容已变化；旧任务已停止推进。"
        return True, ""

    def _enqueue_batch(self, run_payload: dict[str, Any], batch: dict[str, Any]) -> None:
        settings = run_payload["request"]
        requested_scope = settings.get("distributionScope")
        distribution_scope = requested_scope if requested_scope in {"private", "classroom", "public"} else "private"
        request = CoursewareSourcePreparationRequest(
            confirmedMaterialKind=settings.get("confirmedMaterialKind"),
            autoGenerate=True,
            autoPublish=True,
            subject=str(settings["subject"]),
            title=str(batch["title"]),
            pageStart=int(batch["pageStart"]),
            pageEnd=int(batch["pageEnd"]),
            sourceTitle=settings.get("sourceTitle") or str(Path(run_payload["filename"]).stem),
            sourceUrl=settings.get("sourceUrl"),
            license=settings.get("license"),
            attributionText=settings.get("attributionText"),
            distributionScope=distribution_scope,
        )
        whole_book = {
            "runId": str(run_payload["runId"]),
            "batchIndex": int(batch["batchIndex"]),
            "pageBudget": MAX_GENERATED_PAGES_PER_BATCH,
        }
        with use_job_runtime_snapshot(run_payload):
            self.material_courses.enqueue(
                str(run_payload["uploadId"]), request,
                idempotency_key=self._batch_key(run_payload, batch),
                whole_book=whole_book,
            )

    def _wait_for_running(
        self,
        run_id: str,
        run_payload: dict[str, Any],
        jobs: list[dict[str, Any]],
        cancellation_check: Any,
    ) -> set[str]:
        """Wait on active child leases without creating polling jobs or retries."""
        active_ids = {str(item["jobId"]) for item in jobs}
        while active_ids:
            if cancellation_check():
                raise JobCancelled()
            if self._is_paused(run_id):
                return active_ids
            changed, _message = self._source_change_state(run_payload)
            if changed:
                return active_ids
            settled: set[str] = set()
            for job_id in active_ids:
                job = self.jobs.get_job(job_id)
                if not job or job.get("status") != "running":
                    settled.add(job_id)
                    continue
                lease = job.get("leaseExpiresAt")
                if lease is not None and float(lease) <= time.time():
                    self.jobs.recover_expired_leases()
                    settled.add(job_id)
            active_ids -= settled
            if active_ids:
                threading.Event().wait(0.25)
        return active_ids

    def _batch_active_jobs(self, run_payload: dict[str, Any], batch: dict[str, Any]) -> list[dict[str, Any]]:
        source_job = self.jobs.get_by_idempotency_key(self._source_job_key(run_payload, batch))
        if not source_job or source_job.get("status") != "succeeded":
            return []
        result = source_job.get("result") if isinstance(source_job.get("result"), dict) else {}
        chapter_id = str(((result.get("chapters") or [{}])[0]).get("chapterId") or "")
        if not chapter_id:
            return []
        plan_job = self.jobs.get_job(str(result.get("planJobId") or "")) if result.get("planJobId") else None
        if plan_job and plan_job.get("status") == "running":
            return [plan_job]
        if plan_job and plan_job.get("status") == "queued":
            return []
        plan = self.courseware.store.load_plan(chapter_id)
        ids = [str(item) for item in (plan or {}).get("coursewareIds", [])]
        active: list[dict[str, Any]] = []
        for courseware_id in ids:
            document = self.courseware.store.load_document(courseware_id)
            if not document:
                continue
            candidates = [document.get("generationJobId"), (document.get("automation") or {}).get("jobId")]
            for job_id in candidates:
                child = self.jobs.get_job(str(job_id or "")) if job_id else None
                if child and child.get("status") == "running":
                    active.append(child)
        return active

    def notify_after_job(self, job_id: str, attempt_count: int) -> dict[str, Any] | None:
        """Queue one resumable controller after a source or courseware stage settles."""
        job = self.jobs.get_job(job_id)
        if not job:
            return None
        payload = job.get("payload") or {}
        whole_book = payload.get("wholeBook") if isinstance(payload, dict) else None
        if isinstance(whole_book, dict):
            run_id = str(whole_book.get("runId") or "")
            batch_index = int(whole_book.get("batchIndex", 0))
        else:
            chapter_id = str(payload.get("chapterId") or "") if isinstance(payload, dict) else ""
            if not chapter_id and payload.get("coursewareId"):
                document = self.courseware.store.load_document(str(payload["coursewareId"]))
                chapter_id = str((document or {}).get("chapterId") or "")
            chapter = self.store.load_chapter(chapter_id) if chapter_id else None
            source_job_id = str((chapter or {}).get("sourcePreparationJobId") or "")
            source_job = self.jobs.get_job(source_job_id) if source_job_id else None
            source_workflow = ((source_job or {}).get("payload") or {}).get("wholeBook")
            if not isinstance(source_workflow, dict):
                return None
            run_id = str(source_workflow.get("runId") or "")
            batch_index = int(source_workflow.get("batchIndex", 0))
        if not run_id or not self.jobs.get_job(run_id):
            return None
        return self.jobs.create_job(
            ADVANCE_JOB_TYPE,
            {"runId": run_id, "batchIndex": batch_index, "triggerJobId": job_id, "triggerAttempt": int(attempt_count)},
            idempotency_key=f"whole-book-advance:{run_id}:notify:{job_id}:{int(attempt_count)}"[:255],
            max_attempts=100,
            reject_payload_mismatch=True,
        )

    def run_control(self, payload: dict[str, Any], _cancellation_check: Any, _context: JobExecutionContext) -> dict[str, Any]:
        return {"runId": str(payload["runId"]), "action": str(payload["action"])}
