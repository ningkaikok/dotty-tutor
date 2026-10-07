"""Deterministic, source-grounded chapter course workflow."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
import uuid
from functools import wraps
from typing import Any

from answer_evaluator import evaluate_structured_answer
from application.job_worker import CancellableJobResult
from application.services.chapter_source_preview import file_hash, source_file
from domain.chapters.english import evaluate_english_chapter_attempt
from domain.chapters.quality import (
    ChapterDraftValidationError,
    build_quality_draft,
    draft_source_excerpt,
    quality_draft_schema,
)
from domain.chapters.source import fingerprint, pages_from_ocr, sentences
from domain.chapters.templates import build_chapter_lessons
from domain.questions.student_view import student_question_payload


def _serialize_chapter_mutation(method):
    """Hold a PostgreSQL advisory lock across every write in a chapter workflow."""
    @wraps(method)
    def serialized(self, chapter_id: str, *args, **kwargs):
        with self.store.chapter_lock(chapter_id):
            with self.store.atomic():
                return method(self, chapter_id, *args, **kwargs)
    return serialized


def _strip_answers(value: Any) -> Any:
    if isinstance(value, dict):
        projected = {key: _strip_answers(item) for key, item in value.items()
                if key not in {"answerSpec", "correctAnswer", "correctAnswers", "acceptedAnswers", "teacherVariants",
                               "proposedVariants", "answerDraft", "rubric", "rubricDraft", "solution"}}
        if value.get("type") == "quiz" and isinstance(projected.get("payload"), dict):
            projected["payload"] = {key: item for key, item in projected["payload"].items() if key in {"questionId", "prompt"}}
        return projected
    if isinstance(value, list):
        return [_strip_answers(item) for item in value]
    return value


def _safe_runtime_audit(run: dict[str, Any]) -> dict[str, Any]:
    """Keep provider diagnostics without persisting prompt or credential-bearing data."""
    return {
        key: run[key]
        for key in ("requestedProvider", "provider", "model", "promptChars", "maxOutputTokens", "durationMs", "usage", "providerAttempts", "schemaFallback")
        if key in run
    }


def _evidence_options(source_revision_id: str | None, page_source: dict[str, Any]) -> list[dict[str, Any]]:
    page_number = page_source.get("page")
    return [
        {"sourceRevisionId": source_revision_id, "page": page_number,
         "sentenceId": sentence["sentenceId"], "quote": sentence["text"], "label": sentence["text"]}
        for sentence in page_source.get("sentences", [])
    ] + [
        {"sourceRevisionId": source_revision_id, "page": page_number,
         "regionId": region["regionId"], "quote": page_source.get("text", ""),
         "label": f"第 {page_number} 页图像区域"}
        for region in page_source.get("regions", []) if region.get("regionId")
    ]


def _refs_resolve(references: list[dict[str, Any]], revision_id: str, pages: list[dict[str, Any]]) -> bool:
    by_page = {page["page"]: page for page in pages}
    if not references:
        return False
    for reference in references:
        if reference.get("sourceRevisionId") != revision_id:
            return False
        page = by_page.get(reference.get("page"))
        if page is None:
            return False
        region_id = reference.get("regionId")
        sentence_id = reference.get("sentenceId")
        if region_id and region_id not in {region.get("regionId") for region in page.get("regions", [])}:
            return False
        if sentence_id and sentence_id not in {sentence.get("sentenceId") for sentence in page.get("sentences", [])}:
            return False
        if not region_id and not sentence_id and not reference.get("quote"):
            return False
        quote = reference.get("quote")
        if quote and not isinstance(quote, str):
            return False
        if quote:
            source_text = next((sentence["text"] for sentence in page.get("sentences", []) if sentence.get("sentenceId") == sentence_id), page.get("text", ""))
            if quote.strip() != source_text.strip() and quote.strip() not in source_text:
                return False
    return True


def _validate_tutorial(lesson: dict[str, Any], source: dict[str, Any]) -> None:
    """Tutorial approval still requires every teaching block to resolve to its source."""
    blocks = lesson.get("blocks") or []
    teaching = [block for block in blocks if not block["id"].endswith("-scope")]
    if len(teaching) != 4 or (lesson.get("questionPayload") or {}).get("question"):
        raise ValueError("教程内容结构无效，请重新生成讲解草稿")
    for block in teaching:
        payload = block.get("payload") or {}
        if block.get("type") != "markdown" or not payload.get("markdown") or not _refs_resolve(
            payload.get("sourceRefs") or [], source["sourceRevisionId"], source["pages"],
        ):
            raise ValueError("教程讲解引用无法定位到当前来源，请重新生成")


class ChapterCourseService:
    """Manage source revisions, human review, immutable publications and attempts."""

    def __init__(self, store: Any, jobs: Any = None, generation_runtime: Any = None) -> None:
        self.store = store
        self.jobs = jobs
        if generation_runtime is None:
            from infrastructure.runtime.model_runtime import runtime
            generation_runtime = runtime
        self.generation_runtime = generation_runtime

    def enqueue_ai_generation(
        self, chapter_id: str, *, expected_record_version: int,
        runtime_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Queue one AI draft against an immutable chapter and source revision snapshot."""
        if self.jobs is None:
            raise RuntimeError("章节 AI 生成任务队列未装配")
        chapter = self.store.load_chapter(chapter_id)
        if not chapter or chapter.get("deletedAt"):
            raise LookupError("章节不存在")
        if chapter.get("recordVersion") != expected_record_version:
            raise ValueError("章节已被其他编辑更新，请刷新后重试")
        if chapter.get("publications") and chapter.get("currentLessonIds"):
            current_publication = next((item for item in chapter["publications"] if item.get("publicationId") == chapter.get("publicationId")), None)
            if current_publication and current_publication.get("sourceRevisionId") == chapter["sourceRevisions"][-1]["sourceRevisionId"]:
                raise ValueError("当前来源已有发布课程，请先创建来源修订")
        current = {item["lessonId"]: item for item in chapter.get("lessons", []) if item.get("lessonId") in chapter.get("currentLessonIds", [])}
        for item in current.values():
            if item.get("status") not in {"in_review", "needs_review", "draft"} or item.get("reviews"):
                raise ValueError("当前课程已进入审核，不能被 AI 草稿覆盖")
        revision = chapter["sourceRevisions"][-1]
        from infrastructure.runtime.job_snapshot import current_job_runtime_snapshot
        payload = {
            "chapterId": chapter_id,
            "sourceRevisionId": revision["sourceRevisionId"],
            "expectedRecordVersion": expected_record_version,
            "runtimeSnapshot": runtime_snapshot or current_job_runtime_snapshot(),
            "generationKey": f"chapter:{chapter_id}:revision:{revision['sourceRevisionId']}:record:{expected_record_version}:ai-v1",
        }
        idempotency_key = f"chapter:{chapter_id}:revision:{revision['sourceRevisionId']}:record:{expected_record_version}:ai-v1"
        # Model calls can consume quota; a new attempt requires an explicit teacher retry.
        return self.jobs.create_job("chapter.lesson.generate", payload, idempotency_key=idempotency_key, max_attempts=1)

    def run_ai_generation(
        self, payload: dict[str, Any], *, cancellation_check: Any = lambda: False,
    ) -> dict[str, Any] | CancellableJobResult:
        """Generate outside chapter locks, then compare-and-write the reviewed draft."""
        from application.job_worker import (
            JobCancelled,
            RetryableJobError,
            TerminalJobError,
        )
        from infrastructure.runtime.job_snapshot import use_job_runtime_snapshot
        from infrastructure.runtime.model_runtime import ModelSelection

        chapter_id = str(payload["chapterId"])
        chapter = self.store.load_chapter(chapter_id)
        if not chapter or chapter.get("deletedAt"):
            raise TerminalJobError("章节不存在")
        revision_id = str(payload["sourceRevisionId"])
        expected_version = payload.get("expectedRecordVersion")
        if not isinstance(expected_version, int) or isinstance(expected_version, bool):
            raise TerminalJobError("章节版本快照无效")
        generation_key = str(payload.get("generationKey") or f"chapter:{chapter_id}:revision:{revision_id}:record:{expected_version}:ai-v1")
        completed = (chapter.get("aiGenerationRuns") or {}).get(generation_key)
        if isinstance(completed, dict):
            result_payload = completed.get("result")
            return result_payload if isinstance(result_payload, dict) else {str(key): value for key, value in completed.items()}
        source = next((item for item in chapter["sourceRevisions"] if item["sourceRevisionId"] == revision_id), None)
        if source is None or chapter["sourceRevisions"][-1]["sourceRevisionId"] != revision_id:
            raise TerminalJobError("来源已修订，请基于最新版本重新生成")
        if chapter.get("recordVersion") != expected_version:
            raise TerminalJobError("章节已编辑或审核，请刷新后重新发起生成")
        if cancellation_check():
            raise JobCancelled()
        excerpt = draft_source_excerpt(source)
        schema = quality_draft_schema(chapter["subject"], source=excerpt, teaching_mode=chapter.get("teachingMode", "practice"))
        prompt = (
            "为教师编辑工作台生成中文课程草稿。只能依据 SOURCE_JSON，不得补造教材事实、数学条件或答案。引用必须使用 SOURCE_JSON 中现有的 sentenceId，不得自行编造或重新编号。每条 citations 必须含 sourceRevisionId/page/sentenceId/regionId/quote 五字段；无值填 null，sentenceId 或 regionId 至少有一个非空，quote 固定填 null，服务端会按有效句子 ID 填入原文；不要重新抄写或改写 OCR 引文。每段内容都必须提供 citations。所有输出只是待教师复核的草稿。\n"
            "数学须提供 concept/conditions/example(prompt,answer,steps,citations)、恰好三级 hints(每项 text,citations)、check(prompt,answer,citations)。条件缺失或无法据来源作答时拒绝生成。\n"
            "英语须提供四题 questions，kind 分别为 word_meaning/reference/explicit/inference，每项含 prompt、answer、citations、teacherVariants（建议答案变体）和 rubric（评分要点）；变体与rubric明确是待教师审核建议。推断题也须引用原文依据。\n"
            f"CHAPTER_JSON={json.dumps({'subject': chapter['subject'], 'title': chapter['title']}, ensure_ascii=False)}\n"
            f"SOURCE_JSON={json.dumps(excerpt, ensure_ascii=False)}"
        )
        if chapter.get("teachingMode") == "tutorial":
            prompt = (
                "依据 SOURCE_JSON 为教师生成一个连贯的中文教程章节，不出题、不生成答案或评分标准。"
                "先规划学习目标，再依次讲解概念或方法、用教材中的示例示范，最后总结。"
                "输出 sections，恰好四项，kind 依次为 objectives/explanation/example/summary，"
                "每项含 title/text/citations。每项内容须由来源支持，不得编造事实或例子。"
                "citations 使用已有 sourceRevisionId/page/sentenceId，regionId 和 quote 填 null。"
                "只生成待教师复核的草稿。\n"
                f"CHAPTER_JSON={json.dumps({'subject': chapter['subject'], 'title': chapter['title']}, ensure_ascii=False)}\n"
                f"SOURCE_JSON={json.dumps(excerpt, ensure_ascii=False)}"
            )
        schema_fingerprint = hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        prompt_fingerprint = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        runtime_run: dict[str, Any] = {}
        stage = "model_generation"
        try:
            selection_config = (payload.get("runtimeSnapshot") or {}).get("generation") or {}
            provider = selection_config.get("provider")
            if provider not in {"ollama", "codex", "deepseek", "mock"} or not selection_config.get("model"):
                raise ValueError("后台任务 Runtime 选择快照无效")
            with use_job_runtime_snapshot(payload):
                draft, runtime_run = self.generation_runtime.generate_json(
                    prompt, schema, max_tokens=2400, task="chapter_lesson_draft",
                    selection=ModelSelection(provider, str(selection_config["model"])),
                )
            stage = "draft_validation"
            documents, issues = build_quality_draft(chapter, source, draft)
            excerpt_pages = [page["page"] for page in excerpt["pages"]]
            if sum(len(page.get("sentences", [])) for page in excerpt["pages"]) < sum(len(page.get("sentences", [])) for page in source["pages"]):
                for document in documents:
                    document["blocks"].insert(0, {"id": f"{document['lessonId']}-scope", "type": "markdown", "title": "本次教学范围",
                        "payload": {"markdown": f"本草稿使用第 {excerpt_pages[0]}–{excerpt_pages[-1]} 页的起始段落节选，尚未覆盖整章。完整章节原文已保留，请教师复核后发布。"}})

        except JobCancelled:
            raise
        except Exception as error:
            safe_runtime = getattr(error, "runtime_run", None)
            if isinstance(safe_runtime, dict):
                runtime_run = safe_runtime
            message = f"章节草稿来源校验失败：{error}" if isinstance(error, ChapterDraftValidationError) else "章节草稿生成或来源校验失败"
            raise RetryableJobError(message, details={
                "stage": stage, "errorType": type(error).__name__,
                "sourceFingerprint": source.get("fingerprint"), "promptSha256": prompt_fingerprint,
                "schemaSha256": schema_fingerprint, "runtime": _safe_runtime_audit(runtime_run),
            }) from error
        if cancellation_check():
            raise JobCancelled()
        prior_ids: list[str] = []
        prior_status = str(chapter.get("status") or "in_review")
        prior_review_issues = copy.deepcopy(chapter.get("reviewIssues") or [])
        persisted_version = int(expected_version)
        with self.store.chapter_lock(chapter_id):
            with self.store.atomic():
                current_chapter = self.store.load_chapter(chapter_id)
                if not current_chapter or current_chapter.get("recordVersion") != expected_version or current_chapter["sourceRevisions"][-1]["sourceRevisionId"] != revision_id:
                    raise TerminalJobError("生成期间章节或来源已变化，草稿未写入")
                current_items = [item for item in current_chapter.get("lessons", []) if item.get("lessonId") in current_chapter.get("currentLessonIds", [])]
                if any(item.get("status") not in {"in_review", "needs_review", "draft"} or item.get("reviews") for item in current_items):
                    raise TerminalJobError("生成期间教师已开始审核，草稿未覆盖现有课程")
                new_ids = [doc["lessonId"] for doc in documents]
                prior_ids = list(current_chapter.get("currentLessonIds", []))
                prior_status = str(current_chapter.get("status") or "in_review")
                prior_review_issues = copy.deepcopy(current_chapter.get("reviewIssues") or [])
                for old in current_items:
                    old["supersededByGeneration"] = True
                for document in documents:
                    self.store.save_lesson(document)
                current_chapter["lessons"].extend({"lessonId": document["lessonId"], "sourceRevisionId": revision_id,
                    "status": "in_review", "sourceLocator": document["sourceLocator"], "reviewIssues": document["reviewIssues"],
                    "generationMethod": "ai_source_constrained", "reviews": []} for document in documents)
                current_chapter["currentLessonIds"] = new_ids
                current_chapter["reviewIssues"] = list(current_chapter.get("reviewIssues", [])) + issues
                current_chapter["status"] = "in_review"
                result = {"chapterId": chapter_id, "sourceRevisionId": revision_id, "lessonIds": new_ids,
                          "status": "in_review", "humanReviewRequired": True}
                current_chapter.setdefault("aiGenerationRuns", {})[generation_key] = {
                    "result": result,
                    "audit": {"sourceRevisionId": revision_id, "sourceFingerprint": source.get("fingerprint"),
                              "promptSha256": prompt_fingerprint, "schemaSha256": schema_fingerprint,
                              "runtime": _safe_runtime_audit(runtime_run), "createdAt": time.time()},
                }
                self.store.save_chapter(current_chapter)
                persisted_version = int(current_chapter["recordVersion"])

        def rollback_cancelled_generation() -> None:
            """Compensate only while no teacher has touched this exact generation."""
            with self.store.chapter_lock(chapter_id):
                with self.store.atomic():
                    latest = self.store.load_chapter(chapter_id)
                    if (not latest or latest.get("recordVersion") != persisted_version
                            or latest.get("currentLessonIds") != new_ids):
                        return
                    latest["lessons"] = [item for item in latest.get("lessons", []) if item.get("lessonId") not in new_ids]
                    for item in latest.get("lessons", []):
                        if item.get("lessonId") in prior_ids:
                            item.pop("supersededByGeneration", None)
                    latest["currentLessonIds"] = prior_ids
                    latest["status"] = prior_status
                    latest["reviewIssues"] = prior_review_issues
                    latest.get("aiGenerationRuns", {}).pop(generation_key, None)
                    self.store.save_chapter(latest)
                    for document in documents:
                        saved = self.store.load_lesson(document["lessonId"])
                        if saved:
                            saved["status"] = "archived"
                            self.store.save_lesson(saved)

        return CancellableJobResult(result, on_cancel=rollback_cancelled_generation)

    def _normalize_source(self, raw: dict[str, Any]) -> dict[str, Any]:
        source = dict(raw)
        pages = list(source.get("pages") or [])
        job = None
        if source.get("uploadId"):
            job = self.store.load_job(str(source["uploadId"]))
            if not job:
                raise LookupError("上传任务不存在")
            if job.get("status") not in {"complete", "completed", "ready"}:
                raise ValueError("上传或 OCR 任务尚未完成")
            persisted_pages = pages_from_ocr(str(job.get("sourceText") or ""))
            if pages:
                persisted_by_page = {int(item["page"]): str(item["text"]).strip() for item in persisted_pages}
                for page in pages:
                    stored_text = persisted_by_page.get(int(page["page"]))
                    if not stored_text or " ".join(str(page.get("text") or "").split()) != " ".join(stored_text.split()):
                        raise ValueError(f"第 {page['page']} 页文本与上传任务中的 OCR 快照不一致")
            else:
                pages = persisted_pages
        if not pages:
            raise ValueError("来源缺少已完成 OCR 的逐页文本")
        flag_map = {int(item["page"]): list(item.get("flags") or []) for item in source.get("pageFlags") or []}
        if any(page_number < (source.get("pageStart") or 1) or page_number > (source.get("pageEnd") or 100_000)
               for page_number in flag_map):
            raise ValueError("页标记必须位于本次导入页段内")
        for page in pages:
            page["flags"] = list(dict.fromkeys([*(page.get("flags") or []), *flag_map.get(int(page["page"]), [])]))
        pages.sort(key=lambda item: int(item["page"]))
        page_numbers = [int(item["page"]) for item in pages]
        if len(page_numbers) != len(set(page_numbers)):
            raise ValueError("来源页码重复")
        start = source.get("pageStart") or page_numbers[0]
        end = source.get("pageEnd") or page_numbers[-1]
        if end < start or end - start + 1 > 80:
            raise ValueError("章节页段必须在 1 到 80 页之间")
        pages = [page for page in pages if start <= int(page["page"]) <= end]
        page_numbers = [int(page["page"]) for page in pages]
        if start > end or not page_numbers:
            raise ValueError("来源页码范围无效")
        missing = sorted(set(range(start, end + 1)) - set(page_numbers))
        normalized_pages = []
        for page in pages:
            text = str(page.get("text") or "").strip()
            regions = [dict(region) for region in page.get("regions") or []]
            if any(region["x"] + region["width"] > 1 or region["y"] + region["height"] > 1 for region in regions):
                raise ValueError("来源区域必须位于归一化页面边界内")
            region_ids = [region.get("regionId") for region in regions if region.get("regionId")]
            if len(region_ids) != len(set(region_ids)):
                raise ValueError("同一页来源区域 ID 不能重复")
            normalized_pages.append({
                "page": int(page["page"]), "text": text,
                "regions": regions,
                "flags": list(page.get("flags") or []),
                "sentences": [],
            })
        original = source_file(self.store, job) if job is not None else None
        original_hash = file_hash(original) if original is not None else None
        return {
            "sourceFileSha256": original_hash,
            "uploadId": source.get("uploadId"), "sourceVersion": str(source.get("sourceVersion") or "1"),
            "license": source.get("license"),
            "pageStart": start, "pageEnd": end, "pages": normalized_pages,
            "fingerprint": fingerprint({"sourceVersion": source.get("sourceVersion") or "1", "license": source.get("license"), "pages": normalized_pages, "sourceFileSha256": original_hash}),
            "missingPages": missing,
        }

    def create(self, request: dict[str, Any]) -> dict[str, Any]:
        source = self._normalize_source(request["source"])
        chapter_id = uuid.uuid4().hex
        revision_id = uuid.uuid4().hex
        for page in source["pages"]:
            page["sentences"] = sentences(page["text"], page["page"], revision_id)
        issues = self._source_issues(source)
        chapter = {
            "chapterId": chapter_id, "subject": request["subject"], "title": request["title"],
            "teachingMode": request.get("teachingMode", "practice"),
            "status": "needs_review" if issues else "draft", "version": 1, "recordVersion": 1,
            "sourceRevisions": [{"sourceRevisionId": revision_id, **source, "issues": issues, "createdAt": time.time()}],
            "lessons": [], "currentLessonIds": [], "reviewIssues": issues,
            "publicationId": None, "publications": [], "createdAt": time.time(),
        }
        self.store.create_chapter(chapter)
        return chapter

    @_serialize_chapter_mutation
    def delete(self, chapter_id: str) -> dict[str, str]:
        """Hide a course while retaining sources, publications and learner history."""
        chapter = self.store.load_chapter(chapter_id)
        if not chapter or chapter.get("deletedAt"):
            raise LookupError("课程不存在或已删除")
        lookup = getattr(self.jobs, "latest_for_payload", None)
        current = lookup("chapter.lesson.generate", "chapterId", chapter_id) if callable(lookup) else None
        if isinstance(current, dict) and current["status"] in {"queued", "running"}:
            self.jobs.request_cancel(current["jobId"])
        chapter["deletedAt"] = time.time()
        self.store.save_chapter(chapter)
        return {"status": "deleted", "chapterId": chapter_id}

    def get(self, chapter_id: str) -> dict[str, Any]:
        chapter = self.store.load_chapter(chapter_id)
        if not chapter or chapter.get("deletedAt"):
            raise LookupError("章节不存在")
        result = copy.deepcopy(chapter)
        result.pop("aiGenerationRuns", None)
        lookup_job = getattr(self.jobs, "latest_for_payload", None)
        latest_job = lookup_job("chapter.lesson.generate", "chapterId", chapter_id) if callable(lookup_job) else None
        result["generationJobId"] = latest_job.get("jobId") if isinstance(latest_job, dict) else None
        for revision in result["sourceRevisions"]:
            for page in revision["pages"]:
                page["previewUrl"] = (
                    f"/api/chapters/{chapter_id}/sources/{revision['sourceRevisionId']}/pages/{page['page']}/preview"
                    if revision.get("sourceFileSha256") else None
                )
        merged = []
        for item in chapter.get("lessons", []):
            lesson = self.store.load_lesson(item["lessonId"]) or dict(item)
            lesson.update({key: value for key, value in item.items() if key in {"sourceRevisionId", "sourceLocator", "reviewIssues", "reviews"}})
            revision = next((rev for rev in chapter["sourceRevisions"] if rev["sourceRevisionId"] == lesson.get("sourceRevisionId")), None)
            locator = lesson.get("sourceLocator") or {}
            question = (lesson.get("questionPayload") or {}).get("question") or {}
            referenced_pages = {reference.get("page") for reference in question.get("requiredEvidenceRefs", []) if isinstance(reference, dict)}
            referenced_pages.update(ref.get("page") for block in lesson.get("blocks", []) for ref in block.get("payload", {}).get("sourceRefs", []))
            referenced_pages.add(locator.get("page"))
            source_pages = [page for page in (revision or {}).get("pages", []) if page.get("page") in referenced_pages]
            lesson["evidenceOptions"] = [
                option for page in source_pages
                for option in _evidence_options(lesson.get("sourceRevisionId"), page)
            ]
            merged.append(lesson)
        result["lessons"] = merged
        return result

    @_serialize_chapter_mutation
    def revise(self, chapter_id: str, request: dict[str, Any]) -> dict[str, Any]:
        chapter = self.store.load_chapter(chapter_id)
        if not chapter or chapter.get("deletedAt"):
            raise LookupError("章节不存在")
        if request.get("expectedRecordVersion") is not None and request["expectedRecordVersion"] != chapter["recordVersion"]:
            raise ValueError("章节已被其他编辑更新，请刷新后重试")
        source = self._normalize_source(request["source"])
        prior = chapter["sourceRevisions"][-1]
        if source["fingerprint"] == prior["fingerprint"]:
            raise ValueError("来源内容未发生变化")
        issues = self._source_issues(source)
        revision = {"sourceRevisionId": uuid.uuid4().hex, **source, "issues": issues, "createdAt": time.time()}
        for page in revision["pages"]:
            page["sentences"] = sentences(page["text"], page["page"], revision["sourceRevisionId"])
        chapter["sourceRevisions"].append(revision)
        chapter["version"] += 1
        chapter["currentLessonIds"] = []
        chapter["reviewIssues"] = issues
        chapter["status"] = "needs_review" if issues else "draft"
        # Draft lessons tied to an older source must be explicitly regenerated or reviewed.
        for item in chapter.get("lessons", []):
            if item.get("status") in {"draft", "in_review", "needs_review", "approved"}:
                item["status"] = "needs_review"
                item.setdefault("reviewIssues", []).append({"code": "source_revision_changed", "message": "来源已修订，需要重新核对"})
                lesson = self.store.load_lesson(item["lessonId"])
                if lesson:
                    lesson["status"] = "needs_review"
                    self.store.save_lesson(lesson)
        self.store.save_chapter(chapter)
        return self.get(chapter_id)

    @staticmethod
    def _source_issues(source: dict[str, Any]) -> list[dict[str, str]]:
        issues = [{"code": "missing_page", "message": f"页段缺页：{page}"} for page in source["missingPages"]]
        if not source.get("license"):
            issues.append({"code": "source_license_missing", "message": "请记录来源许可或材料授权依据"})
        for page in source["pages"]:
            if not page["text"]:
                issues.append({"code": "missing_source_text", "message": f"第 {page['page']} 页没有可核对文本"})
            for flag in page["flags"]:
                mapped = {
                    "missing_conditions": "missing_condition", "wrong_figure": "wrong_figure",
                    "missing_page": "missing_page", "unreadable": "unreadable_source",
                }.get(flag)
                if mapped:
                    issues.append({"code": mapped, "message": f"第 {page['page']} 页需要复核：{flag}"})
            if re.search(r"(?:若|如果|当|且|并且|其中|满足条件)\s*$", page["text"]):
                issues.append({"code": "incomplete_condition", "message": f"第 {page['page']} 页结尾可能缺少条件或结论"})
        return issues

    @_serialize_chapter_mutation
    def generate(self, chapter_id: str) -> dict[str, Any]:
        chapter = self.store.load_chapter(chapter_id)
        if not chapter or chapter.get("deletedAt"):
            raise LookupError("章节不存在")
        source = chapter["sourceRevisions"][-1]
        if chapter.get("currentLessonIds"):
            return self.get(chapter_id)
        if chapter.get("teachingMode") == "tutorial":
            raise ValueError("教程请使用 AI 讲解生成，模板出题不适用于教程")
        documents, issues = build_chapter_lessons(chapter, source)
        revision_id = source["sourceRevisionId"]
        lesson_ids = [document["lessonId"] for document in documents]
        for document in documents:
            self.store.save_lesson(document)
        chapter["currentLessonIds"] = lesson_ids
        chapter["reviewIssues"] = issues
        chapter["status"] = "in_review"
        chapter["lessons"].extend(
            {
                "lessonId": document["lessonId"], "sourceRevisionId": revision_id, "status": "in_review",
                "sourceLocator": document["sourceLocator"],
                "reviewIssues": [],
            }
            for document in documents
        )
        self.store.save_chapter(chapter)
        return self.get(chapter_id)

    @_serialize_chapter_mutation
    def review(self, chapter_id: str, lesson_id: str, decision: str, reviewer: str, note: str = "", expected_record_version: int | None = None) -> dict[str, Any]:
        chapter = self.store.load_chapter(chapter_id)
        if not chapter or chapter.get("deletedAt"):
            raise LookupError("章节不存在")
        if expected_record_version is not None and expected_record_version != chapter["recordVersion"]:
            raise ValueError("章节已被其他编辑更新，请刷新后重试")
        if lesson_id not in chapter.get("currentLessonIds", []):
            raise LookupError("当前来源修订中找不到该课程")
        lesson = self.store.load_lesson(lesson_id)
        if not lesson:
            raise LookupError("课程不存在")
        payload = lesson.get("questionPayload") or {}
        question = payload.get("question") or {}
        quality = payload.get("quality") or {}
        ai_review_pending = str(quality.get("reviewBasis") or "").startswith("ai_")
        if decision == "approve" and chapter.get("teachingMode") == "tutorial":
            _validate_tutorial(lesson, chapter["sourceRevisions"][-1])
            payload["quality"] = {"status": "ready", "errors": [], "reviewBasis": "teacher_approved_tutorial", "reviewer": reviewer}
            lesson["questionPayload"] = payload
        if decision == "approve" and ai_review_pending and chapter.get("teachingMode") != "tutorial":
            accepted = question.get("acceptedAnswers") or question.get("correctAnswers") or []
            expected_answer = (question.get("answerSpec") or {}).get("expected")
            if not accepted and expected_answer is not None and str(expected_answer).strip():
                accepted = [str(expected_answer)]
            if not question.get("prompt") or not accepted:
                raise ValueError("请先核对并填写题目与候选答案")
            revision = next((item for item in chapter["sourceRevisions"] if item["sourceRevisionId"] == question.get("sourceRevisionId")), None)
            pages = [{"sourceRevisionId": question.get("sourceRevisionId"), **page} for page in (revision or {}).get("pages", [])]
            if not _refs_resolve(question.get("requiredEvidenceRefs") or [], str(question.get("sourceRevisionId") or ""), pages):
                raise ValueError("请先核对答案引用是否属于对应来源页")
            if chapter["subject"] == "english":
                question["acceptedAnswers"] = list(dict.fromkeys(accepted))
                question["correctAnswers"] = list(dict.fromkeys(accepted))
                if question.get("variantReviewStatus") != "teacher_confirmed":
                    question["teacherVariants"] = []
                question["variantReviewStatus"] = "teacher_approved"
                rubric = dict(question.get("rubric") or {})
                rubric["supportStatus"] = "supported"
                question["rubric"] = rubric
            payload["quality"] = {"status": "ready", "errors": [], "reviewBasis": "teacher_approved_ai_draft", "reviewer": reviewer}
            lesson["questionPayload"] = payload
        lesson.setdefault("reviews", []).append({"reviewer": reviewer, "decision": decision, "note": note, "createdAt": time.time()})
        lesson["status"] = "approved" if decision == "approve" else "needs_review"
        self.store.save_lesson(lesson)
        for item in chapter["lessons"]:
            if item["lessonId"] == lesson_id:
                item["status"] = lesson["status"]
                item["reviews"] = lesson["reviews"]
        chapter["status"] = "in_review"
        self.store.save_chapter(chapter)
        return self.get(chapter_id)

    @_serialize_chapter_mutation
    def edit_lesson(self, chapter_id: str, lesson_id: str, request: dict[str, Any]) -> dict[str, Any]:
        """Save a teacher-authored question and answer tied to a current source page."""
        chapter = self.store.load_chapter(chapter_id)
        if not chapter or lesson_id not in chapter.get("currentLessonIds", []):
            raise LookupError("当前修订中找不到该课程")
        if request.get("expectedRecordVersion") is not None and request["expectedRecordVersion"] != chapter["recordVersion"]:
            raise ValueError("章节已被其他编辑更新，请刷新后重试")
        if chapter.get("teachingMode") == "tutorial":
            raise ValueError("教程没有检查题，请重新生成讲解草稿")
        revision = chapter["sourceRevisions"][-1]
        if request["sourceRevisionId"] != revision["sourceRevisionId"] or not any(
            page["page"] == request["page"] for page in revision["pages"]
        ):
            raise ValueError("题目依据必须来自当前来源修订和已导入页码")
        lesson = self.store.load_lesson(lesson_id)
        if not lesson:
            raise LookupError("课程不存在")
        payload = lesson.get("questionPayload") or {}
        question = payload.get("question") or {}
        question["prompt"] = request["prompt"]
        question["sourceRevisionId"] = revision["sourceRevisionId"]
        page_data = next(page for page in revision["pages"] if page["page"] == request["page"])
        locator = {"sourceRevisionId": revision["sourceRevisionId"], "page": request["page"], "regions": page_data["regions"]}
        default_sentence = page_data.get("sentences", [None])[0]
        default_refs = [{
            "sourceRevisionId": revision["sourceRevisionId"], "page": request["page"],
            "sentenceId": default_sentence["sentenceId"], "quote": default_sentence["text"],
        }] if default_sentence else [{
            "sourceRevisionId": revision["sourceRevisionId"], "page": request["page"], "quote": page_data["text"],
        }]
        required_refs = request.get("requiredEvidenceRefs") or default_refs
        if not _refs_resolve(required_refs, revision["sourceRevisionId"], revision["pages"]):
            raise ValueError("答案依据必须指向当前来源中的句子或区域")
        question["sourceLocator"] = locator
        question["subject"] = chapter["subject"]
        if chapter["subject"] == "english":
            accepted = request.get("acceptedAnswers") or [request["answer"]]
            teacher_variants = request.get("teacherVariants")
            if teacher_variants is not None:
                if not isinstance(teacher_variants, list) or any(not isinstance(item, str) or not item.strip() for item in teacher_variants):
                    raise ValueError("教师确认的英语答案变体格式无效")
                accepted = list(dict.fromkeys([request["answer"], *[item.strip() for item in teacher_variants]]))
                question["teacherVariants"] = [item.strip() for item in teacher_variants]
                question["variantReviewStatus"] = "teacher_confirmed"
            question.update({
                "questionKind": request["questionKind"], "answerMode": request["answerMode"],
                "acceptedAnswers": accepted, "correctAnswers": [request["answer"]], "requiredEvidenceRefs": required_refs,
                "rubric": request.get("rubric") or {"supportStatus": "needs_review" if request["questionKind"] == "inference" else "supported"},
            })
        else:
            question["requiredEvidenceRefs"] = required_refs
        if chapter["subject"] == "math" and request["answerType"] == "numeric":
            question["questionType"] = "numeric"
            question["answerSpec"] = {"expected": request["answer"], "answerType": "numeric"}
            question.pop("correctAnswers", None)
            question.pop("evaluation", None)
        else:
            question["questionType"] = "short-answer"
            question["evaluation"] = {"mode": "deterministic"}
            question["correctAnswers"] = (question["acceptedAnswers"] if chapter["subject"] == "english"
                                          else [request["answer"]])
            question.pop("answerSpec", None)
        old_quality = payload.get("quality") or {}
        if str(old_quality.get("reviewBasis") or "").startswith("ai_"):
            payload["quality"] = {"status": "needs_review", "errors": ["教师已编辑，请确认检查题、提示和来源依据"], "reviewBasis": "ai_edited_pending_approval"}
        else:
            payload["quality"] = {"status": "ready", "errors": [], "reviewBasis": "teacher_authored"}
        lesson["questionPayload"] = payload
        lesson["sourceRevisionId"] = revision["sourceRevisionId"]
        lesson["sourceLocator"] = question["sourceLocator"]
        for block in lesson.get("blocks", []):
            # English reading blocks are immutable excerpts, not authored explanations.
            if chapter["subject"] == "math" and block.get("type") == "markdown" and request.get("conceptMarkdown") is not None:
                block["payload"]["markdown"] = request["conceptMarkdown"]
                block["payload"]["text"] = request["conceptMarkdown"]
            if block.get("type") == "annotation" and request.get("exampleText") is not None:
                block["payload"]["text"] = request["exampleText"]
            if block.get("type") == "hint":
                level = int(block.get("payload", {}).get("level", 1))
                reviewed_hints = request.get("hints")
                if isinstance(reviewed_hints, list) and level <= len(reviewed_hints):
                    block["payload"]["hint"] = reviewed_hints[level - 1]
                elif request.get("hint") is not None and level == 1:
                    block["payload"]["hint"] = request["hint"]
            block.setdefault("payload", {})["sourceLocator"] = question["sourceLocator"]
            if block.get("type") == "quiz":
                block["payload"]["questionId"] = lesson_id
                block["payload"]["prompt"] = request["prompt"]
        lesson["status"] = "in_review"
        lesson.pop("reviews", None)
        lesson["reviewIssues"] = []
        self.store.save_lesson(lesson)
        chapter["reviewIssues"] = [issue for issue in chapter.get("reviewIssues", []) if issue.get("lessonId") != lesson_id]
        for item in chapter["lessons"]:
            if item["lessonId"] == lesson_id:
                item["status"] = "in_review"
                item.pop("reviews", None)
                item["reviewIssues"] = []
        self.store.save_chapter(chapter)
        return self.get(chapter_id)

    @_serialize_chapter_mutation
    def publish(self, chapter_id: str) -> dict[str, Any]:
        chapter = self.store.load_chapter(chapter_id)
        if not chapter or chapter.get("deletedAt"):
            raise LookupError("章节不存在")
        source = chapter["sourceRevisions"][-1]
        if chapter.get("publicationId") and chapter.get("status") == "published" and chapter["currentLessonIds"]:
            return {"chapterId": chapter_id, "publicationId": chapter["publicationId"], "version": chapter["version"], "status": "published"}
        if source.get("issues") or not chapter.get("currentLessonIds"):
            raise ValueError("来源缺页、缺文本或含待处理视觉/条件问题，不能发布")
        if chapter.get("reviewIssues"):
            raise ValueError("课程存在待处理复核项，不能发布")
        lessons = [self.store.load_lesson(lesson_id) for lesson_id in chapter["currentLessonIds"]]
        if any(not lesson or lesson.get("status") != "approved" for lesson in lessons):
            raise ValueError("所有课程都必须由教师明确批准后才能发布")
        if any((lesson.get("questionPayload") or {}).get("quality", {}).get("status") != "ready" for lesson in lessons):
            raise ValueError("存在缺少客观答案的检查题，请教师补充后再发布")
        for lesson in lessons:
            if chapter.get("teachingMode") == "tutorial":
                _validate_tutorial(lesson, source)
                continue
            question = (lesson.get("questionPayload") or {}).get("question") or {}
            revision = next((item for item in chapter["sourceRevisions"] if item["sourceRevisionId"] == question.get("sourceRevisionId")), None)
            pages = [{"sourceRevisionId": question.get("sourceRevisionId"), **page} for page in (revision or {}).get("pages", [])]
            revision_id = question.get("sourceRevisionId")
            if not isinstance(revision_id, str) or not _refs_resolve(question.get("requiredEvidenceRefs") or [], revision_id, pages):
                raise ValueError(f"课程 {lesson['lessonId']} 的答案依据无法定位到当前来源")
        if chapter["subject"] == "english" and chapter.get("teachingMode") != "tutorial":
            for lesson in lessons:
                question = (lesson.get("questionPayload") or {}).get("question") or {}
                revision = next((item for item in chapter["sourceRevisions"] if item["sourceRevisionId"] == question.get("sourceRevisionId")), None)
                pages = [{"sourceRevisionId": question.get("sourceRevisionId"), **page} for page in (revision or {}).get("pages", [])]
                accepted = question.get("acceptedAnswers") or []
                check = evaluate_english_chapter_attempt(
                    question=question, answer={"value": accepted[0]} if accepted else {},
                    evidence_refs=question.get("requiredEvidenceRefs") or [], source_pages=pages,
                )
                if check["assessment"] != "correct":
                    raise ValueError(f"课程 {lesson['lessonId']} 的答案依据尚未审核通过")
        now = time.time()
        publication = self.store.create_chapter_publication(
            publication_id=uuid.uuid4().hex, title=chapter["title"],
            source_upload_id=source.get("uploadId"), lesson_ids=chapter["currentLessonIds"],
            status="draft", created_at=now, version=chapter["version"],
            revision_of=chapter.get("publicationId"),
        )
        self.store.update_publication_status(publication["publicationId"], "in_review")
        publication = self.store.update_publication_status(publication["publicationId"], "published")
        chapter["publicationId"] = publication["publicationId"]
        chapter["status"] = "published"
        for item in chapter["lessons"]:
            if item["lessonId"] in chapter["currentLessonIds"]:
                item["status"] = "published"
        chapter["publications"].append({
            "publicationId": publication["publicationId"], "version": chapter["version"],
            "sourceRevisionId": source["sourceRevisionId"], "publishedAt": now,
        })
        self.store.save_chapter(chapter)
        return {"chapterId": chapter_id, "publicationId": publication["publicationId"], "version": publication["version"], "status": "published"}

    def get_published(self, chapter_id: str, publication_id: str | None = None) -> dict[str, Any]:
        chapter = self.store.load_chapter(chapter_id)
        if not chapter or chapter.get("deletedAt") or not chapter.get("publicationId"):
            raise LookupError("已发布章节不存在")
        publication_id = publication_id or chapter["publicationId"]
        if publication_id not in {item["publicationId"] for item in chapter.get("publications", [])}:
            raise LookupError("该发布版本不属于此章节")
        publication = self.store.load_publication(publication_id)
        if not publication or publication.get("status") != "published":
            raise LookupError("已发布章节不存在")
        lessons = []
        for lesson in publication["lessons"]:
            question = (lesson.get("questionPayload") or {}).get("question") or {}
            if chapter.get("teachingMode") == "tutorial":
                refs = [ref for block in lesson["blocks"] for ref in block.get("payload", {}).get("sourceRefs", [])]
                first = refs[0]
                question = {"sourceRevisionId": first["sourceRevisionId"], "sourceLocator": {"sourceRevisionId": first["sourceRevisionId"], "page": first["page"], "regions": []}, "requiredEvidenceRefs": refs}
            source_revision = next((item for item in chapter["sourceRevisions"] if item["sourceRevisionId"] == question.get("sourceRevisionId")), None)
            locator = question.get("sourceLocator") or {}
            referenced_pages = {reference.get("page") for reference in question.get("requiredEvidenceRefs", []) if isinstance(reference, dict)}
            referenced_pages.add(locator.get("page"))
            source_pages = [item for item in (source_revision or {}).get("pages", []) if item.get("page") in referenced_pages]
            public = {
                "lessonId": lesson["lessonId"], "title": lesson["title"], "version": lesson["version"],
                "status": lesson["status"], "knowledgePoints": lesson["knowledgePoints"],
                "blocks": _strip_answers(lesson["blocks"]),
                "questionPayload": ({"question": None} if chapter.get("teachingMode") == "tutorial" else student_question_payload(lesson.get("questionPayload"))),
                "sourceRevisionId": question.get("sourceRevisionId"),
                "sourceLocator": question.get("sourceLocator"),
                "evidenceOptions": [
                    option for page in source_pages
                    for option in _evidence_options(question.get("sourceRevisionId"), page)
                ],
            }
            lessons.append(public)
        return {"chapterId": chapter_id, "subject": chapter["subject"], "title": chapter["title"], "teachingMode": chapter.get("teachingMode", "practice"),
                "publicationId": publication["publicationId"], "version": publication["version"],
                "status": "published", "lessons": lessons}

    def attempt(self, chapter_id: str, request: dict[str, Any]) -> dict[str, Any]:
        chapter = self.store.load_chapter(chapter_id)
        if not chapter or chapter.get("deletedAt"):
            raise LookupError("章节不存在")
        if chapter.get("teachingMode") == "tutorial":
            raise ValueError("教程没有检查题，无需提交作答")
        publication_id = request.get("publicationId") or chapter.get("publicationId")
        if publication_id not in {item["publicationId"] for item in chapter.get("publications", [])}:
            raise LookupError("该发布版本不属于此章节")
        publication = self.store.load_publication(publication_id)
        if not publication or publication.get("status") != "published":
            raise LookupError("已发布章节版本不存在")
        lesson = next((item for item in publication["lessons"] if item["lessonId"] == request["lessonId"]), None)
        question = (lesson.get("questionPayload") or {}).get("question") if lesson else None
        if not lesson or not question or request["questionId"] != lesson["lessonId"] or question.get("id") != request["questionId"]:
            raise LookupError("题目不属于该发布版本")
        revision_id = question.get("sourceRevisionId")
        source_revision = next((item for item in chapter["sourceRevisions"] if item["sourceRevisionId"] == revision_id), None)
        source_pages = [{"sourceRevisionId": revision_id, **page} for page in (source_revision or {}).get("pages", [])]
        existing = self.store.get_chapter_attempt(request["attemptId"])
        if existing:
            expected = {
                "subject": chapter["subject"], "publicationId": publication_id,
                "learnerId": request["learnerId"], "lessonId": request["lessonId"],
                "answer": request["answer"], "evidenceRefs": request.get("evidenceRefs", []),
            }
            if any(existing.get(key) != value for key, value in expected.items()):
                raise ValueError("attemptId 已用于其他作答")
            return self._resolved_english_attempt(existing) if chapter["subject"] == "english" else existing

        base = {
            "attemptId": request["attemptId"], "subject": chapter["subject"],
            "publicationId": publication_id, "learnerId": request["learnerId"],
            "lessonId": request["lessonId"], "answer": request["answer"],
            "evidenceRefs": request.get("evidenceRefs", []),
        }
        if chapter["subject"] == "english":
            evaluated = evaluate_english_chapter_attempt(
                question=question, answer=request["answer"],
                evidence_refs=request.get("evidenceRefs", []), source_pages=source_pages,
            )
            evaluated["feedback"]["evidence"] = evaluated.pop("evaluationEvidence", {})
            return self._resolved_english_attempt(self.store.save_chapter_attempt({**base, **evaluated}))

        expected_refs = question.get("requiredEvidenceRefs") or []
        evidence_refs = request.get("evidenceRefs", [])
        evidence_verdict = "missing" if not evidence_refs else "supported"
        if not expected_refs:
            evidence_verdict = "needs_review"
        else:
            def ref_key(ref: dict[str, Any]) -> tuple[Any, ...]:
                return tuple(ref.get(key) for key in ("sourceRevisionId", "page", "regionId", "sentenceId"))
            required_keys = {ref_key(ref) for ref in expected_refs}
            received_keys = {ref_key(ref) for ref in evidence_refs}
            if not required_keys.issubset(received_keys):
                evidence_verdict = "mismatch" if received_keys else "missing"
        for ref in evidence_refs:
            page = next((item for item in source_pages if item["page"] == ref.get("page")), None)
            if not page or ref.get("sourceRevisionId") != revision_id:
                evidence_verdict = "mismatch"
                break
            region_id = ref.get("regionId")
            if region_id and region_id not in {item.get("regionId") for item in page.get("regions", [])}:
                evidence_verdict = "mismatch"
                break
            sentence_id = ref.get("sentenceId")
            sentence = next((item for item in page.get("sentences", []) if item.get("sentenceId") == sentence_id), None) if sentence_id else None
            if sentence_id and sentence is None:
                evidence_verdict = "mismatch"
                break
            quote = ref.get("quote")
            if quote is not None:
                if not isinstance(quote, str):
                    evidence_verdict = "mismatch"
                    break
                canonical_text = sentence.get("text") if sentence else page.get("text", "")
                if quote.strip() != canonical_text.strip() and quote.strip() not in canonical_text:
                    evidence_verdict = "mismatch"
                    break
        if evidence_verdict != "supported":
            assessment = "incorrect" if evidence_verdict == "mismatch" else "needs_review"
            return self.store.save_chapter_attempt({**base, "assessment": assessment, "evidenceVerdict": evidence_verdict,
                "feedback": {"message": "请提供与题目要求一致的来源依据。"}})

        answer_text = str(request["answer"].get("text") or request["answer"].get("numericAnswer") or request["answer"].get("value") or "")
        evaluated = evaluate_structured_answer(question, answer_text, request["answer"])
        if evaluated is None:
            return self.store.save_chapter_attempt({**base, "assessment": "needs_review", "evidenceVerdict": "supported",
                "feedback": {"message": "当前判题规则无法确定，请交教师复核。"}})
        feedback = {"message": evaluated.get("reply", "已记录作答。"), "evidence": evaluated.get("evaluationEvidence")}
        session_id = "chapter-" + hashlib.sha256(f"{publication_id}\0{request['learnerId']}".encode()).hexdigest()[:48]
        session = self.store.get_learning_session(session_id)
        if not session:
            try:
                self.store.create_chapter_learning_session(session_id=session_id, learner_id=request["learnerId"], publication_id=publication_id, started_at=time.time())
            except Exception:
                session = self.store.get_learning_session(session_id)
                if not session:
                    raise
        submission = {**base, "assessment": evaluated["assessment"], "evidenceVerdict": "supported", "feedback": feedback}
        response = {
            "text": answer_text,
            "interactionResult": request["answer"],
            "_evidenceRefs": request.get("evidenceRefs", []),
        }
        result = self.store.record_exercise_attempt(
            attempt_id=request["attemptId"], session_id=session_id, question_id=request["questionId"],
            response=response, assessment=evaluated["assessment"], hint_level=0, duration_ms=0,
            created_at=time.time(), chapter_submission=submission,
        )
        persisted = self.store.get_chapter_attempt(request["attemptId"])
        if not persisted:
            raise RuntimeError("章节作答未能与学习证据一同持久化")
        return {**persisted, "mastery": result.get("mastery")}

    @staticmethod
    def _resolved_english_attempt(result: dict[str, Any]) -> dict[str, Any]:
        reviews = result.get("reviews") or []
        if reviews:
            latest = reviews[-1]
            result["assessment"] = latest["decision"]
            result["feedback"] = {**(result.get("feedback") or {}), "reviewer": latest["reviewer"], "reviewNote": latest["note"]}
        return result

    def review_attempt(self, chapter_id: str, attempt_id: str, reviewer: str, decision: str, note: str) -> dict[str, Any]:
        chapter = self.store.load_chapter(chapter_id)
        attempt = self.store.get_chapter_english_attempt(attempt_id)
        if not chapter or not attempt or attempt["publicationId"] not in {item["publicationId"] for item in chapter.get("publications", [])}:
            raise LookupError("英语作答记录不存在")
        if decision == "correct" and attempt.get("evidenceVerdict") != "supported":
            raise ValueError("来源依据未匹配，不能人工确认答对")
        reviewed = self.store.review_chapter_english_attempt(attempt_id, reviewer, decision, note)
        return self._resolved_english_attempt(reviewed)

    def list(self) -> dict[str, Any]:
        chapters = [chapter for chapter in self.store.list_chapters() if not chapter.get("deletedAt")]
        for chapter in chapters:
            origin = chapter["sourceRevisions"][0]
            chapter["uploadId"] = origin.get("uploadId")
            chapter["pageStart"] = origin.get("pageStart")
        return {"items": [{key: value for key, value in item.items() if key not in {"sourceRevisions", "lessons", "publications"}} for item in chapters]}

    def get_attempt(self, chapter_id: str, attempt_id: str, learner_id: str) -> dict[str, Any]:
        chapter = self.store.load_chapter(chapter_id)
        if not chapter or chapter.get("deletedAt"):
            raise LookupError("章节不存在")
        result = self.store.get_chapter_attempt(attempt_id)
        if not result or result.get("learnerId") != learner_id or result.get("publicationId") not in {item["publicationId"] for item in chapter.get("publications", [])}:
            raise LookupError("作答记录不存在")
        return self._resolved_english_attempt(result) if result.get("subject") == "english" else result

    def get_attempt_for_review(self, chapter_id: str, attempt_id: str) -> dict[str, Any]:
        chapter = self.store.load_chapter(chapter_id)
        result = self.store.get_chapter_attempt(attempt_id)
        if not chapter or not result or result.get("publicationId") not in {
            item.get("publicationId") for item in chapter.get("publications", [])
        }:
            raise LookupError("章节作答记录不存在")
        return self._resolved_english_attempt(result) if result.get("subject") == "english" else result
