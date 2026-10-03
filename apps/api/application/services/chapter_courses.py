"""Deterministic, source-grounded chapter course workflow."""

from __future__ import annotations

import copy
import hashlib
import re
import time
import uuid
from functools import wraps
from typing import Any

from answer_evaluator import evaluate_structured_answer
from domain.chapters.english import evaluate_english_chapter_attempt
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
        return {key: _strip_answers(item) for key, item in value.items()
                if key not in {"answerSpec", "correctAnswer", "correctAnswers", "solution", "rubric"}}
    if isinstance(value, list):
        return [_strip_answers(item) for item in value]
    return value


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


class ChapterCourseService:
    """Manage source revisions, human review, immutable publications and attempts."""

    def __init__(self, store: Any) -> None:
        self.store = store

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
        return {
            "uploadId": source.get("uploadId"), "sourceVersion": str(source.get("sourceVersion") or "1"),
            "license": source.get("license"),
            "pageStart": start, "pageEnd": end, "pages": normalized_pages,
            "fingerprint": fingerprint({"sourceVersion": source.get("sourceVersion") or "1", "license": source.get("license"), "pages": normalized_pages}),
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
            "status": "needs_review" if issues else "draft", "version": 1, "recordVersion": 1,
            "sourceRevisions": [{"sourceRevisionId": revision_id, **source, "issues": issues, "createdAt": time.time()}],
            "lessons": [], "currentLessonIds": [], "reviewIssues": issues,
            "publicationId": None, "publications": [], "createdAt": time.time(),
        }
        self.store.create_chapter(chapter)
        return chapter

    def get(self, chapter_id: str) -> dict[str, Any]:
        chapter = self.store.load_chapter(chapter_id)
        if not chapter:
            raise LookupError("章节不存在")
        result = copy.deepcopy(chapter)
        merged = []
        for item in chapter.get("lessons", []):
            lesson = self.store.load_lesson(item["lessonId"]) or dict(item)
            lesson.update({key: value for key, value in item.items() if key in {"sourceRevisionId", "sourceLocator", "reviewIssues", "reviews"}})
            revision = next((rev for rev in chapter["sourceRevisions"] if rev["sourceRevisionId"] == lesson.get("sourceRevisionId")), None)
            locator = lesson.get("sourceLocator") or {}
            page_source = next((page for page in (revision or {}).get("pages", []) if page["page"] == locator.get("page")), {})
            lesson["evidenceOptions"] = _evidence_options(lesson.get("sourceRevisionId"), page_source)
            merged.append(lesson)
        result["lessons"] = merged
        return result

    @_serialize_chapter_mutation
    def revise(self, chapter_id: str, request: dict[str, Any]) -> dict[str, Any]:
        chapter = self.store.load_chapter(chapter_id)
        if not chapter:
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
        if not chapter:
            raise LookupError("章节不存在")
        source = chapter["sourceRevisions"][-1]
        if chapter.get("currentLessonIds"):
            return self.get(chapter_id)
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
        if not chapter:
            raise LookupError("章节不存在")
        if expected_record_version is not None and expected_record_version != chapter["recordVersion"]:
            raise ValueError("章节已被其他编辑更新，请刷新后重试")
        if lesson_id not in chapter.get("currentLessonIds", []):
            raise LookupError("当前来源修订中找不到该课程")
        lesson = self.store.load_lesson(lesson_id)
        if not lesson:
            raise LookupError("课程不存在")
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
            question.update({
                "questionKind": request["questionKind"], "answerMode": request["answerMode"],
                "acceptedAnswers": accepted, "requiredEvidenceRefs": required_refs,
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
            question["correctAnswers"] = request.get("acceptedAnswers") or [request["answer"]]
            question.pop("answerSpec", None)
        payload["quality"] = {"status": "ready", "errors": [], "reviewBasis": "teacher_authored"}
        lesson["questionPayload"] = payload
        lesson["sourceRevisionId"] = revision["sourceRevisionId"]
        lesson["sourceLocator"] = question["sourceLocator"]
        for block in lesson.get("blocks", []):
            if block.get("type") == "markdown" and request.get("conceptMarkdown") is not None:
                block["payload"]["markdown"] = request["conceptMarkdown"]
                block["payload"]["text"] = request["conceptMarkdown"]
            if block.get("type") == "annotation" and request.get("exampleText") is not None:
                block["payload"]["text"] = request["exampleText"]
            if block.get("type") == "hint" and request.get("hint") is not None:
                block["payload"]["hint"] = request["hint"]
            block.setdefault("payload", {})["sourceLocator"] = question["sourceLocator"]
            if block.get("type") == "quiz":
                block["payload"]["questionId"] = lesson_id
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
        if not chapter:
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
            question = (lesson.get("questionPayload") or {}).get("question") or {}
            revision = next((item for item in chapter["sourceRevisions"] if item["sourceRevisionId"] == question.get("sourceRevisionId")), None)
            pages = [{"sourceRevisionId": question.get("sourceRevisionId"), **page} for page in (revision or {}).get("pages", [])]
            revision_id = question.get("sourceRevisionId")
            if not isinstance(revision_id, str) or not _refs_resolve(question.get("requiredEvidenceRefs") or [], revision_id, pages):
                raise ValueError(f"课程 {lesson['lessonId']} 的答案依据无法定位到当前来源")
        if chapter["subject"] == "english":
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
        if not chapter or not chapter.get("publicationId"):
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
            source_revision = next((item for item in chapter["sourceRevisions"] if item["sourceRevisionId"] == question.get("sourceRevisionId")), None)
            locator = question.get("sourceLocator") or {}
            page_source = next((item for item in (source_revision or {}).get("pages", []) if item["page"] == locator.get("page")), {})
            public = {
                "lessonId": lesson["lessonId"], "title": lesson["title"], "version": lesson["version"],
                "status": lesson["status"], "knowledgePoints": lesson["knowledgePoints"],
                "blocks": _strip_answers(lesson["blocks"]),
                "questionPayload": student_question_payload(lesson.get("questionPayload")),
                "sourceRevisionId": question.get("sourceRevisionId"),
                "sourceLocator": question.get("sourceLocator"),
                "evidenceOptions": _evidence_options(question.get("sourceRevisionId"), page_source),
            }
            lessons.append(public)
        return {"chapterId": chapter_id, "subject": chapter["subject"], "title": chapter["title"],
                "publicationId": publication["publicationId"], "version": publication["version"],
                "status": "published", "lessons": lessons}

    def attempt(self, chapter_id: str, request: dict[str, Any]) -> dict[str, Any]:
        chapter = self.store.load_chapter(chapter_id)
        if not chapter:
            raise LookupError("章节不存在")
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
        chapters = self.store.list_chapters()
        return {"items": [{key: value for key, value in item.items() if key not in {"sourceRevisions", "lessons", "publications"}} for item in chapters]}

    def get_attempt(self, chapter_id: str, attempt_id: str, learner_id: str) -> dict[str, Any]:
        chapter = self.store.load_chapter(chapter_id)
        if not chapter:
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
