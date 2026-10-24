"""Unattended courseware checking, bounded repair and atomic publication.

All model work happens outside transactions. Each resulting action is fenced by
the Worker lease and the exact document version; manual work always takes over.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select

from application.job_worker import JobCancelled, JobExecutionContext, RetryableJobError
from application.services.courseware_ai_review import PAGE_PROMPT_ID
from domain.contracts.courseware import CoursewareDocument
from domain.courseware.ai_review import input_hash
from domain.courseware.quality import document_page_issues, source_readiness_issues
from infrastructure.runtime.job_snapshot import (
    current_job_runtime_snapshot,
    use_job_runtime_snapshot,
)
from persistence.schema import background_jobs

MAX_REPAIRS_PER_PAGE = 2


class CoursewareAutomationService:
    """Use the existing reviewer and generation jobs; never impersonate a teacher."""

    JOB_TYPE = "courseware.automation"

    def __init__(self, *, courseware: Any, reviewer: Any) -> None:
        self.courseware = courseware
        self.reviewer = reviewer
        self.store = courseware.store
        self.jobs = courseware.jobs

    def _save(self, document: dict[str, Any]) -> dict[str, Any]:
        document["updatedAt"] = time.time()
        return self.store.save_record(
            record_id=f"document:{document['coursewareId']}", record_type="document",
            chapter_id=document["chapterId"], courseware_id=document["coursewareId"],
            source_revision_id=document["sourceRevisionId"], status=document["status"],
            payload=CoursewareDocument.model_validate(document).model_dump(),
            expected_version=int(document["recordVersion"]),
        )

    def enqueue(self, document: dict[str, Any]) -> dict[str, Any]:
        """Called in the generation transaction so the handoff survives crashes."""
        state = document.get("automation")
        if not state or state["status"] in {"paused", "ready", "needs_attention"}:
            return document
        template = self.courseware._prompt(PAGE_PROMPT_ID)
        with self.store.atomic() as connection:
            job = self.jobs.create_job(self.JOB_TYPE, {
                "coursewareId": document["coursewareId"],
                "chapterId": document["chapterId"],
                "sourceRevisionId": document["sourceRevisionId"],
                "planVersion": document["planVersion"],
                "documentVersion": int(document["recordVersion"]) + 1,
                "pageIds": [page["pageId"] for page in document["pages"]],
                "reviewer": self.reviewer._reviewer(),
                "frozenTemplate": self.courseware._frozen_template(template),
                "runtimeSnapshot": current_job_runtime_snapshot(),
            }, idempotency_key=f"courseware-auto:{document['coursewareId']}:{document['recordVersion']}",
                max_attempts=2, connection=connection, reject_payload_mismatch=True)
            state.update(status="checking", jobId=job["jobId"], message="正在自动检查内容、答案与教材原页。")
            return self._save(document)

    def _current(self, payload: dict[str, Any], context: JobExecutionContext, connection: Any) -> tuple[Any, Any, Any] | None:
        live = connection.execute(select(background_jobs.c.job_id).where(
            background_jobs.c.job_id == context.job_id,
            background_jobs.c.status == "running",
            background_jobs.c.lease_owner == context.worker_id,
            background_jobs.c.attempt_count == context.attempt_count,
            background_jobs.c.lease_expires_at > time.time(),
            background_jobs.c.cancel_requested.is_(False),
        ).with_for_update()).scalar_one_or_none()
        if live is None:
            raise JobCancelled()
        chapter, source, document = self.courseware._document(payload["coursewareId"], lock=True)
        state = document.get("automation")
        if (not state or state.get("jobId") != context.job_id or state["status"] != "checking"
                or int(document["recordVersion"]) != int(payload["documentVersion"])):
            return None
        return chapter, source, document

    def _attention(self, document: dict[str, Any], message: str) -> dict[str, Any]:
        document["automation"].update(status="needs_attention", message=message[:1000])
        return self._save(document)

    @staticmethod
    def _included_source_pages(selected: list[dict[str, Any]], skipped_pages: set[int]) -> list[dict[str, Any]]:
        """Remove only skips whose ledger was validated against this frozen source revision."""
        return [page for page in selected if int(page["page"]) not in skipped_pages]

    def run(self, payload: dict[str, Any], cancellation_check: Any, context: JobExecutionContext) -> dict[str, Any]:
        """Check every page, repair only failures, then reuse the publication gate."""
        courseware_id = payload["coursewareId"]
        document = self.courseware.get_document(courseware_id)
        if (int(document["recordVersion"]) != int(payload["documentVersion"])
                or (document.get("automation") or {}).get("status") != "checking"):
            return {"coursewareId": courseware_id, "status": "superseded"}
        if cancellation_check():
            raise JobCancelled()
        # Exact-input review caching also survives a Worker retry halfway through.
        with use_job_runtime_snapshot(payload):
            self.reviewer.run(payload, cancellation_check, context)
        if cancellation_check():
            raise JobCancelled()
        try:
            return self._finish(payload, context)
        except ValueError as error:
            # Publication validation rolls back approvals and snapshots together.
            with self.courseware.chapters.store.chapter_lock(document["chapterId"]):
                with self.store.atomic() as connection:
                    current = self._current(payload, context, connection)
                    if current:
                        self._attention(current[2], str(error))
            return {"coursewareId": courseware_id, "status": "needs_attention"}

    def _finish(self, payload: dict[str, Any], context: JobExecutionContext) -> dict[str, Any]:
        initial = self.store.load_document(payload["coursewareId"])
        if not initial:
            raise LookupError("课件不存在")
        with self.courseware.chapters.store.chapter_lock(initial["chapterId"]):
            with self.store.atomic() as connection:
                current = self._current(payload, context, connection)
                if not current:
                    return {"coursewareId": payload["coursewareId"], "status": "superseded"}
                chapter, source, document = current
                skipped_pages = self.courseware._verified_whole_book_skip_pages(document, source)
                selected = self.courseware._selected_pages(
                    source, document["pageStart"], document["pageEnd"], allowed_skip_pages=skipped_pages,
                )
                teaching_pages = self._included_source_pages(selected, skipped_pages)
                source_issues = source_readiness_issues(teaching_pages)
                if source_issues:
                    self._attention(document, "; ".join(issue["message"] for issue in source_issues))
                    return {"status": "needs_attention"}
                issues = document_page_issues(self.courseware._with_asset_urls(document), teaching_pages)
                reasons: dict[str, list[str]] = {}
                for issue in issues:
                    reasons.setdefault(str(issue["pageId"]), []).append(str(issue["message"]))
                template = payload["frozenTemplate"]
                for page in document["pages"]:
                    sources = self.reviewer._page_sources(source, document, page)
                    digest = input_hash(self.reviewer._subject(page), sources,
                        prompt_version=template["version"], reviewer=payload["reviewer"])
                    review = self.reviewer.store.find("courseware_page", f"{document['coursewareId']}:{page['pageId']}", digest)
                    if not review:
                        raise RetryableJobError("部分页面检查尚未完成，系统将自动重试。")
                    # Text-only fallback remains useful advice, but cannot clear
                    # diagrams or formulae unseen by the reviewer.
                    if {item["page"] for item in sources} - set(review.get("imagePages", [])):
                        self._attention(document, "部分教材原页暂时无法读取，自动检查未完成。请检查教材原件后重试。")
                        return {"status": "needs_attention"}
                    if review["verdict"] != "pass" or review.get("issues"):
                        reasons.setdefault(page["pageId"], []).append(review["summary"])
                        reasons[page["pageId"]].extend(item["message"] for item in review.get("issues", []))
                if reasons:
                    page_id = next(iter(reasons))
                    state = document["automation"]
                    repairs = state["repairs"]
                    reason = "; ".join(reasons[page_id])[:1000]
                    if repairs.get(page_id, 0) >= MAX_REPAIRS_PER_PAGE:
                        self._attention(document, f"已自动修复两次，仍需处理：{reason}")
                        return {"status": "needs_attention"}
                    repairs[page_id] = repairs.get(page_id, 0) + 1
                    state.update(status="repairing", message="正在自动修复问题页，其余页面会保留。")
                    saved = self._save(document)
                    with use_job_runtime_snapshot(payload):
                        self.courseware.regenerate_page(document["coursewareId"], page_id,
                            expected_record_version=saved["recordVersion"], reason=reason,
                            command_id=f"auto-{context.job_id}-{page_id}", _chapter_lock_held=True)
                    return {"status": "repairing"}
                document["automation"].update(status="ready", message="教程已完成，可直接打开授课。")
                for page in document["pages"]:
                    page["reviewStatus"] = "approved"
                    for reveal in page.get("revealSteps", []):
                        reveal["reviewStatus"] = "approved"
                document.update(status="ready", reviewStatus="ready", reviewIssues=[])
                document["reviewRecords"] = [*document.get("reviewRecords", []), {
                    "actorId": "system:courseware-automation", "decision": "automated_approve",
                    "note": "结构校验与原页 AI 检查通过，自动完成教程；不计为人工复核。",
                    "pageIds": [page["pageId"] for page in document["pages"]], "createdAt": time.time(),
                }][-500:]
                saved = self._save(document)
                publication = self.courseware._publish_locked(connection, chapter, source, saved,
                    expected_record_version=saved["recordVersion"])
                return {"coursewareId": document["coursewareId"], "publicationId": publication["publicationId"], "status": "ready"}
