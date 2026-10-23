"""AI pre-review of courseware pages and advisory opinions on source cases.

The reviewer model only reports findings. This service never approves a page,
decides a source case, or counts anything as a human gold label. Manual routing
uses ``domain.courseware.ai_review``; unattended production consumes the same
evidence through the separate courseware automation service.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from application.job_worker import JobCancelled, JobExecutionContext, TerminalJobError
from application.services.chapter_source_preview import ChapterSourcePreview
from domain.contracts.ai_review import AiReviewModelOutput
from domain.contracts.courseware import CoursewarePage
from domain.courseware.ai_review import (
    agreement_metrics,
    input_hash,
    is_sampled,
    reviewable_page,
    route_page,
)
from domain.courseware.model_output import generation_schema
from infrastructure.runtime.job_snapshot import current_job_runtime_snapshot
from infrastructure.runtime.model_runtime import runtime as generation_runtime
from persistence.ai_review_store import AiReviewStore

PAGE_PROMPT_ID = "courseware.ai_review"
CASE_PROMPT_ID = "quality_review.ai_opinion"
MAX_IMAGES_PER_PAGE = 3
DEFAULT_SAMPLING_RATE = 0.1


def _sampling_rate_from_env() -> float:
    try:
        value = float(os.getenv("COURSEWARE_AI_REVIEW_SAMPLE_RATE", DEFAULT_SAMPLING_RATE))
    except ValueError:
        return DEFAULT_SAMPLING_RATE
    return min(1.0, max(0.0, value))


class CoursewareAiReviewService:
    def __init__(self, *, courseware: Any, store: AiReviewStore, jobs: Any, runtime: Any = generation_runtime,
                 quality_review: Any | None = None, sampling_rate: float | None = None) -> None:
        self.courseware = courseware
        self.store = store
        self.jobs = jobs
        self.runtime = runtime
        self.quality_review = quality_review
        self.sampling_rate = _sampling_rate_from_env() if sampling_rate is None else sampling_rate

    # --- shared --------------------------------------------------------------------------

    @staticmethod
    def _reviewer() -> dict[str, str]:
        review = current_job_runtime_snapshot()["review"]
        return {"provider": str(review["provider"]), "model": str(review["model"])}

    def _call_reviewer(self, payload: dict[str, Any], prompt: str, images: list[Path]) -> tuple[AiReviewModelOutput, dict[str, Any]]:
        reviewer = payload["reviewer"]
        output, run = self.runtime.generate_json_as(
            reviewer["provider"], reviewer["model"], prompt, generation_schema(AiReviewModelOutput),
            2500, images,
        )
        return AiReviewModelOutput.model_validate(output), run

    @staticmethod
    def _review_record(parsed: AiReviewModelOutput, run: dict[str, Any], *, prompt_version: str,
                       text_only: bool, image_pages: list[int]) -> dict[str, Any]:
        return {
            "summary": parsed.summary, "issues": [issue.model_dump() for issue in parsed.issues],
            "model": {"provider": str(run.get("actualProvider") or run.get("provider") or ""),
                      "model": str(run.get("actualModel") or run.get("model") or "")},
            "promptVersion": prompt_version, "textOnly": text_only, "imagePages": image_pages,
            "durationMs": run.get("durationMs"),
        }

    def _latest_job(self, job_type: str, field: str, value: str) -> dict[str, Any] | None:
        return self.jobs.latest_for_payload(job_type, field, value) if self.jobs is not None else None

    def _active_job(self, job_type: str, field: str, value: str) -> str | None:
        job = self._latest_job(job_type, field, value)
        return str(job["jobId"]) if job and job.get("status") in {"queued", "running"} else None

    def _enqueue(self, job_type: str, field: str, value: str, payload: dict[str, Any]) -> str:
        """Reuse a running job; otherwise enqueue a new one that can follow a failed attempt.

        The key binds the request content and the previous attempt, so a double click
        creates one job while a retry after failure is not blocked by the old key.
        """
        latest = self._latest_job(job_type, field, value)
        if latest and latest.get("status") in {"queued", "running"}:
            return str(latest["jobId"])
        content = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        key = f"{job_type}:{value}:{content}:{latest['jobId'] if latest else 'first'}"
        job = self.jobs.create_job(job_type, payload, idempotency_key=key, max_attempts=2)
        return str(job["jobId"])

    # --- courseware pages ----------------------------------------------------------------

    @staticmethod
    def _subject(page: dict[str, Any]) -> dict[str, Any]:
        """Normalize through the page contract first so defaults added on save never look like edits."""
        return reviewable_page(CoursewarePage.model_validate(page).model_dump(mode="json"))

    def _page_sources(self, source: dict[str, Any], document: dict[str, Any], page: dict[str, Any]) -> list[dict[str, Any]]:
        selected = self.courseware._selected_pages(source, int(document["pageStart"]), int(document["pageEnd"]))
        cited = {int(ref["page"]) for ref in page.get("sourceRefs", [])}
        return [{"page": int(item["page"]), "text": str(item.get("text") or "")}
                for item in selected if int(item["page"]) in cited]

    def _pending_pages(self, document: dict[str, Any], source: dict[str, Any], reviewer: dict[str, str],
                       prompt_version: str) -> tuple[list[str], int]:
        """Pages still awaiting a teacher whose exact current input has no verdict yet."""
        pending: list[str] = []
        cached = 0
        for page in document.get("pages", []):
            if page.get("generationStatus") != "completed" or page.get("reviewStatus") != "pending":
                continue
            digest = input_hash(self._subject(page), self._page_sources(source, document, page),
                                prompt_version=prompt_version, reviewer=reviewer)
            if self.store.find("courseware_page", f"{document['coursewareId']}:{page['pageId']}", digest):
                cached += 1
            else:
                pending.append(str(page["pageId"]))
        return pending, cached

    def status(self, courseware_id: str) -> dict[str, Any]:
        _, source, document = self.courseware._document(courseware_id)
        template = self.courseware._prompt(PAGE_PROMPT_ID)
        pending, _ = self._pending_pages(document, source, self._reviewer(), template.version)
        pages = document.get("pages", [])
        stored = self.store.latest("courseware_page", [f"{courseware_id}:{page['pageId']}" for page in pages])
        reviews = {key.split(":", 1)[1]: value for key, value in stored.items()}
        views = []
        for page in pages:
            review = reviews.get(page["pageId"])
            sampled = is_sampled(courseware_id, page["pageId"], int(page["version"]), self.sampling_rate)
            views.append({
                "pageId": page["pageId"], "title": page["title"], "pageVersion": int(page["version"]),
                "reviewStatus": page.get("reviewStatus", "pending"), "sampled": sampled,
                "route": route_page(page, review, sampled=sampled),
                "verdict": review["verdict"] if review else None, "summary": review.get("summary", "") if review else "",
                "issues": review.get("issues", []) if review else [],
                "reviewedVersion": review["subjectVersion"] if review else None,
                "textOnly": bool(review.get("textOnly")) if review else False,
                "model": review.get("model") if review else None,
                "reviewedAt": review["createdAt"] if review else None,
            })
        return {
            "coursewareId": courseware_id, "recordVersion": int(document["recordVersion"]),
            "samplingRate": self.sampling_rate, "pagesToReview": len(pending), "pages": views,
            "metrics": agreement_metrics(pages, reviews, document.get("reviewRecords", []),
                                         courseware_id=courseware_id, rate=self.sampling_rate),
            "jobId": self._active_job("courseware.ai_review", "coursewareId", courseware_id),
        }

    def request(self, courseware_id: str, *, expected_record_version: int) -> dict[str, Any]:
        """Queue one reviewer call per page lacking a verdict for its exact current input."""
        _, source, document = self.courseware._document(courseware_id)
        if int(document["recordVersion"]) != expected_record_version:
            raise ValueError("课件已更新，请刷新后再发起 AI 初审")
        if document.get("generationStatus") in {"queued", "running"}:
            raise ValueError("课件仍在生成，完成后再发起 AI 初审")
        reviewer = self._reviewer()
        template = self.courseware._prompt(PAGE_PROMPT_ID)
        pending, cached = self._pending_pages(document, source, reviewer, template.version)
        result = {"coursewareId": courseware_id, "pagesToReview": len(pending), "cachedPages": cached,
                  "jobId": None, "reviewer": reviewer}
        if not pending:
            return result
        payload = {
            "coursewareId": courseware_id, "documentVersion": expected_record_version, "pageIds": pending,
            "reviewer": reviewer, "frozenTemplate": self.courseware._frozen_template(template),
        }
        return {**result, "jobId": self._enqueue("courseware.ai_review", "coursewareId", courseware_id, payload)}

    def _page_images(self, document: dict[str, Any], sources: list[dict[str, Any]]) -> tuple[list[Path], list[int]]:
        """Render cited original pages; any rendering failure degrades to a text-only review."""
        preview = ChapterSourcePreview(self.courseware.chapters.store)
        images: list[Path] = []
        pages: list[int] = []
        for item in sources[:MAX_IMAGES_PER_PAGE]:
            try:
                images.append(preview.get(str(document["chapterId"]), str(document["sourceRevisionId"]), int(item["page"])))
                pages.append(int(item["page"]))
            except (ValueError, LookupError, OSError):
                continue
        return images, pages

    def run(self, payload: dict[str, Any], cancellation_check: Any, context: JobExecutionContext) -> dict[str, Any]:
        template = self.courseware._template_from_payload(payload["frozenTemplate"])
        courseware_id = str(payload["coursewareId"])
        reviewed = 0
        for page_id in payload["pageIds"]:
            if cancellation_check():
                raise JobCancelled()
            try:
                _, source, document = self.courseware._document(courseware_id)
            except LookupError as error:
                raise TerminalJobError(str(error)) from error
            page = next((item for item in document.get("pages", []) if item["pageId"] == page_id), None)
            # A page edited, regenerated or decided since the request needs no stale verdict.
            if not page or page.get("generationStatus") != "completed" or page.get("reviewStatus") != "pending":
                continue
            sources = self._page_sources(source, document, page)
            subject = self._subject(page)
            digest = input_hash(subject, sources, prompt_version=template.version, reviewer=payload["reviewer"])
            subject_id = f"{courseware_id}:{page_id}"
            if self.store.find("courseware_page", subject_id, digest):
                continue
            images, image_pages = self._page_images(document, sources)
            prompt = template.render(
                subject=str(document.get("subject") or ""),
                page=json.dumps(subject, ensure_ascii=False), source=json.dumps(sources, ensure_ascii=False),
            )
            parsed, run = self._call_reviewer(payload, prompt, images)
            self.store.save(
                subject_kind="courseware_page", subject_id=subject_id, subject_version=int(page["version"]),
                input_hash=digest, verdict=parsed.verdict, job_id=context.job_id,
                review=self._review_record(parsed, run, prompt_version=template.version,
                                           text_only=not images, image_pages=image_pages),
            )
            reviewed += 1
        return {"coursewareId": courseware_id, "reviewedPages": reviewed}

    # --- source cases (advisory only) ----------------------------------------------------

    def _case_inputs(self, case: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], Any]:
        subject = {key: case.get(key) for key in ("subject", "sourcePage", "sourceLocator", "input", "expected", "actual")}
        page_number = int(case["sourcePage"])
        try:
            source_page = self._require_quality_review().source_assets.resolve(case["source"], page_number)
            text = source_page["reader"].pages[page_number - 1].extract_text() or ""
        except (ValueError, LookupError, OSError):
            source_page, text = None, ""
        return subject, [{"page": page_number, "text": text}], source_page

    def _require_quality_review(self) -> Any:
        if self.quality_review is None:
            raise LookupError("来源复核服务未启用")
        return self.quality_review

    def case_opinion(self, case_id: str) -> dict[str, Any]:
        case = self._require_quality_review().get_case(case_id)
        latest = self.store.latest("source_case", [case_id]).get(case_id)
        version = int(case["recordVersion"])
        return {
            "caseId": case_id, "caseVersion": version,
            "verdict": latest["verdict"] if latest else None, "summary": latest.get("summary", "") if latest else "",
            "issues": latest.get("issues", []) if latest else [],
            "stale": bool(latest) and int(latest["subjectVersion"]) != version,
            "textOnly": bool(latest.get("textOnly")) if latest else False,
            "model": latest.get("model") if latest else None,
            "reviewedAt": latest["createdAt"] if latest else None,
            "jobId": self._active_job("quality_review.ai_opinion", "caseId", case_id),
        }

    def request_case_opinion(self, case_id: str, *, expected_record_version: int) -> dict[str, Any]:
        case = self._require_quality_review().get_case(case_id)
        if int(case["recordVersion"]) != expected_record_version:
            raise ValueError("复核案例已更新，请刷新后再请求 AI 参考意见")
        reviewer = self._reviewer()
        template = self.courseware._prompt(CASE_PROMPT_ID)
        subject, sources, _ = self._case_inputs(case)
        digest = input_hash(subject, sources, prompt_version=template.version, reviewer=reviewer)
        if self.store.find("source_case", case_id, digest):
            return self.case_opinion(case_id)
        self._enqueue("quality_review.ai_opinion", "caseId", case_id, {
            "caseId": case_id, "caseVersion": expected_record_version, "reviewer": reviewer,
            "frozenTemplate": self.courseware._frozen_template(template),
        })
        return self.case_opinion(case_id)

    def run_case_opinion(self, payload: dict[str, Any], cancellation_check: Any, context: JobExecutionContext) -> dict[str, Any]:
        quality_review = self._require_quality_review()
        case_id = str(payload["caseId"])
        case = quality_review.get_case(case_id)
        if int(case["recordVersion"]) != int(payload["caseVersion"]):
            raise TerminalJobError("复核案例已更新，旧请求不再执行")
        if cancellation_check():
            raise JobCancelled()
        template = self.courseware._template_from_payload(payload["frozenTemplate"])
        subject, sources, source_page = self._case_inputs(case)
        digest = input_hash(subject, sources, prompt_version=template.version, reviewer=payload["reviewer"])
        if self.store.find("source_case", case_id, digest):
            return {"caseId": case_id, "cached": True}
        images: list[Path] = []
        if source_page is not None:
            try:
                images = [quality_review.source_assets.render_preview(source_page)]
            except (ValueError, OSError):
                images = []
        prompt = template.render(case=json.dumps(subject, ensure_ascii=False), source=json.dumps(sources, ensure_ascii=False))
        parsed, run = self._call_reviewer(payload, prompt, images)
        self.store.save(
            subject_kind="source_case", subject_id=case_id, subject_version=int(case["recordVersion"]),
            input_hash=digest, verdict=parsed.verdict, job_id=context.job_id,
            review=self._review_record(parsed, run, prompt_version=template.version, text_only=not images,
                                       image_pages=[int(case["sourcePage"])] if images else []),
        )
        return {"caseId": case_id, "cached": False}
