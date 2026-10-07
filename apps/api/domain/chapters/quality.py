"""Validate and materialize source-constrained AI chapter drafts."""

from __future__ import annotations

import copy
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from domain.contracts.lesson import lesson_document_from_payload

ENGLISH_KINDS = {"word_meaning", "reference", "explicit", "inference"}


def draft_source_excerpt(source: Mapping[str, Any], *, text_budget: int = 12_000) -> dict[str, Any]:
    """Bound a teaching draft to original sentence evidence, retaining IDs and page numbers."""
    pages: list[dict[str, Any]] = []
    remaining = text_budget
    for page in source.get("pages", []):
        selected = []
        for sentence in page.get("sentences", []):
            size = len(sentence["text"])
            if size > remaining:
                break
            selected.append(sentence)
            remaining -= size
        if selected:
            pages.append({"page": page["page"], "text": "\n".join(s["text"] for s in selected),
                          "sentences": selected, "regions": page.get("regions", [])})
        if len(selected) < len(page.get("sentences", [])) or remaining <= 0:
            break
    if not pages:
        raise ChapterDraftValidationError("来源没有可用于草稿的句子证据，请复核原文")
    return {"sourceRevisionId": source["sourceRevisionId"], "pages": pages}


def quality_draft_schema(subject: str, *, source: Mapping[str, Any] | None = None, teaching_mode: str = "practice") -> dict[str, Any]:
    """Return a strict JSON schema accepted by ModelRuntime structured output."""
    citation = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "sourceRevisionId": {"type": "string", "minLength": 1},
            "page": {"type": "integer", "minimum": 1},
            "sentenceId": {"type": ["string", "null"]},
            "regionId": {"type": ["string", "null"]},
            "quote": {"type": ["string", "null"]},
        },
        "required": ["sourceRevisionId", "page", "sentenceId", "regionId", "quote"],
    }
    if source is not None:
        variants = []
        for page in source["pages"]:
            option = copy.deepcopy(citation)
            properties = option["properties"]
            properties["sourceRevisionId"]["enum"] = [source["sourceRevisionId"]]
            properties["page"]["enum"] = [page["page"]]
            properties["sentenceId"] = {"type": "string", "enum": [sentence["sentenceId"] for sentence in page["sentences"]]}
            properties["regionId"] = {"type": "null"}
            properties["quote"] = {"type": "null"}
            variants.append(option)
        citation = {"anyOf": variants}
    citations = {"type": "array", "minItems": 1, "maxItems": 20, "items": citation}

    def section(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {"type": "object", "additionalProperties": False, "properties": properties, "required": required}

    text = {"type": "string", "minLength": 1, "maxLength": 2000}
    if teaching_mode == "tutorial":
        item = section({"kind": {"type": "string", "enum": ["objectives", "explanation", "example", "summary"]},
                        "title": {"type": "string", "minLength": 1, "maxLength": 160},
                        "text": text, "citations": citations}, ["kind", "title", "text", "citations"])
        return section({"sections": {"type": "array", "minItems": 4, "maxItems": 4, "items": item}}, ["sections"])
    if subject == "math":
        cited_text = section({"text": text, "citations": citations}, ["text", "citations"])
        example = section({"prompt": text, "answer": text, "steps": {"type": "array", "minItems": 1, "maxItems": 8, "items": text}, "citations": citations}, ["prompt", "answer", "steps", "citations"])
        check = section({"prompt": text, "answer": text, "citations": citations}, ["prompt", "answer", "citations"])
        return section({"concept": cited_text, "conditions": cited_text, "example": example,
                        "hints": {"type": "array", "minItems": 3, "maxItems": 3, "items": cited_text},
                        "check": check}, ["concept", "conditions", "example", "hints", "check"])
    if subject != "english":
        raise ChapterDraftValidationError("章节学科不受支持")
    item = section({
        "kind": {"type": "string", "enum": sorted(ENGLISH_KINDS)},
        "prompt": text, "answer": text, "citations": citations,
        "teacherVariants": {"type": "array", "maxItems": 12, "items": {"type": "string", "minLength": 1, "maxLength": 300}},
        "rubric": {"type": "array", "minItems": 1, "maxItems": 12, "items": {"type": "string", "minLength": 1, "maxLength": 500}},
    }, ["kind", "prompt", "answer", "citations", "teacherVariants", "rubric"])
    return section({"questions": {"type": "array", "minItems": 4, "maxItems": 4, "items": item}}, ["questions"])


class ChapterDraftValidationError(ValueError):
    """A model draft is incomplete, malformed, or cites unsupported evidence."""


def _text(value: Any, label: str, *, limit: int = 2_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ChapterDraftValidationError(f"{label}缺失或长度无效")
    return value.strip()


def _citations(value: Any, source: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ChapterDraftValidationError("每项生成内容必须提供来源引用")
    pages = {page.get("page"): page for page in source.get("pages", [])}
    result = []
    for raw in value:
        if not isinstance(raw, Mapping) or raw.get("sourceRevisionId") != source.get("sourceRevisionId"):
            raise ChapterDraftValidationError("引用来源版本无效")
        if not isinstance(raw.get("page"), int) or isinstance(raw.get("page"), bool) or raw["page"] < 1:
            raise ChapterDraftValidationError("引用页码无效")
        page = pages.get(raw.get("page"))
        if not page:
            raise ChapterDraftValidationError("引用页码不在来源快照中")
        sentence_id = raw.get("sentenceId")
        region_id = raw.get("regionId")
        sentence = next((item for item in page.get("sentences", []) if item.get("sentenceId") == sentence_id), None)
        region = next((item for item in page.get("regions", []) if item.get("regionId") == region_id), None)
        quote = raw.get("quote")
        if sentence_id and not sentence or region_id and not region:
            raise ChapterDraftValidationError("引用的句子或区域不属于所标页码")
        if not sentence_id and not region_id:
            raise ChapterDraftValidationError("引用必须定位到来源句子或图像区域")
        if quote is not None:
            if not isinstance(quote, str) or not quote.strip() or not sentence or (quote.strip() != sentence.get("text", "").strip() and quote.strip() not in sentence.get("text", "")):
                raise ChapterDraftValidationError("引用文本与来源句子不一致")
        reference = {key: raw[key] for key in ("sourceRevisionId", "page", "sentenceId", "regionId", "quote") if key in raw}
        if quote is None and sentence:
            # Resolve verbatim text from the validated ID; never ask the model to retype OCR.
            reference["quote"] = sentence["text"]
        result.append(reference)
    return result


def _cited_section(raw: Any, name: str, source: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(raw, Mapping):
        raise ChapterDraftValidationError(f"{name}结构无效")
    citations = _citations(raw.get("citations"), source)
    return dict(raw), citations


def _lesson(
    chapter: Mapping[str, Any], source: Mapping[str, Any], *, lesson_id: str,
    page: Mapping[str, Any], question: dict[str, Any], blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    revision_id = str(source["sourceRevisionId"])
    locator = {"sourceRevisionId": revision_id, "page": page["page"], "regions": page.get("regions", [])}
    payload = {"question": {"id": lesson_id, **question, "sourceRevisionId": revision_id, "sourceLocator": locator},
               "lessonSteps": [], "quality": {"status": "needs_review", "reviewBasis": "ai_generated", "errors": ["AI 草稿尚待教师核验内容、答案、提示和引用"]}}
    document = lesson_document_from_payload(payload, source_upload_id=source.get("uploadId"))
    document.update({"lessonId": lesson_id, "title": f"{chapter['title']} · 第 {page['page']} 页",
                     "version": chapter.get("version", 1), "status": "in_review",
                     "knowledgePoints": [str(chapter["title"])], "blocks": blocks,
                     "questionPayload": payload, "sourceRevisionId": revision_id,
                     "sourceLocator": locator, "reviewIssues": [{"code": "ai_draft_requires_review", "message": "AI 生成内容和引用均需教师复核"}]})
    return document


def build_quality_draft(
    chapter: dict[str, Any], source: dict[str, Any], draft: dict[str, Any], *,
    lesson_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Reject unsupported model claims and return only explicitly unapproved lessons."""
    if not isinstance(draft, dict) or not source.get("pages"):
        raise ChapterDraftValidationError("生成结果或来源页无效")
    if chapter.get("teachingMode") == "tutorial":
        tutorial_sections = draft.get("sections")
        kinds = ["objectives", "explanation", "example", "summary"]
        if not isinstance(tutorial_sections, list) or len(tutorial_sections) != 4:
            raise ChapterDraftValidationError("教程须包含学习目标、讲解、示例与总结")
        by_kind = {}
        for raw in tutorial_sections:
            section, refs = _cited_section(raw, "教程内容", source)
            kind = section.get("kind")
            if kind not in kinds or kind in by_kind:
                raise ChapterDraftValidationError("教程环节缺失或重复")
            by_kind[kind] = {"title": _text(section.get("title"), "环节标题", limit=160),
                             "text": _text(section.get("text"), "教程讲解"), "refs": refs}
        lesson_id = lesson_id_factory()
        first_ref = by_kind["objectives"]["refs"][0]
        page = next(page for page in source["pages"] if page["page"] == first_ref["page"])
        return [{"lessonId": lesson_id, "title": str(chapter["title"]), "version": chapter.get("version", 1),
                 "status": "in_review", "sourceUploadId": source.get("uploadId"),
                 "knowledgePoints": [str(chapter["title"])],
                 "sourceRevisionId": source["sourceRevisionId"],
                 "sourceLocator": {"sourceRevisionId": source["sourceRevisionId"], "page": page["page"], "regions": page.get("regions", [])},
                 "blocks": [{"id": f"{lesson_id}-{kind}", "type": "markdown", "title": by_kind[kind]["title"],
                             "payload": {"markdown": by_kind[kind]["text"], "sourceRefs": by_kind[kind]["refs"]}} for kind in kinds],
                 "questionPayload": {"quality": {"status": "needs_review", "reviewBasis": "ai_tutorial", "errors": ["教程讲解与引用待教师复核"]}},
                 "reviewIssues": []}], []
    if chapter.get("subject") == "math":
        sections: dict[str, Any] = {}
        references: dict[str, list[dict[str, Any]]] = {}
        for name in ("concept", "conditions", "example", "check"):
            sections[name], references[name] = _cited_section(draft.get(name), name, source)
        for name in ("text",):
            _text(sections["concept"].get(name), "概念")
            _text(sections["conditions"].get(name), "适用条件")
        _text(sections["example"].get("prompt"), "例题")
        _text(sections["example"].get("answer"), "例题答案")
        _text(sections["check"].get("prompt"), "检查题")
        _text(sections["check"].get("answer"), "检查题答案")
        steps = sections["example"].get("steps")
        if not isinstance(steps, list) or not 1 <= len(steps) <= 8:
            raise ChapterDraftValidationError("例题步骤数量无效")
        steps = [_text(step, "例题步骤") for step in steps]
        hints = draft.get("hints")
        if not isinstance(hints, list) or len(hints) != 3:
            raise ChapterDraftValidationError("必须提供三级提示")
        hint_items = []
        for item in hints:
            section, refs = _cited_section(item, "分层提示", source)
            hint_items.append({"text": _text(section.get("text"), "分层提示"), "sourceRefs": refs})
        page = next(page for page in source["pages"] if page["page"] == references["concept"][0]["page"])
        lesson_id = lesson_id_factory()
        concept = sections["concept"]["text"].strip()
        condition = sections["conditions"]["text"].strip()
        example = sections["example"]
        check = sections["check"]
        blocks = [
            {"id": f"{lesson_id}-concept", "type": "markdown", "title": "概念与适用条件", "payload": {"markdown": f"{concept}\n\n适用条件：{condition}", "sourceRefs": references["concept"] + references["conditions"]}},
            {"id": f"{lesson_id}-example", "type": "annotation", "title": "例题与解析", "payload": {"text": f"{example['prompt']}\n\n解答：{example['answer']}\n\n" + "\n".join(f"{index}. {step}" for index, step in enumerate(steps, 1)), "sourceRefs": references["example"]}},
            *[{"id": f"{lesson_id}-hint-{level}", "type": "hint", "title": f"第 {level} 级提示", "payload": {"level": level, "hint": item["text"], "sourceRefs": item["sourceRefs"]}} for level, item in enumerate(hint_items, 1)],
            {"id": f"{lesson_id}-quiz", "type": "quiz", "title": "检查题", "payload": {"questionId": lesson_id, "prompt": check["prompt"], "sourceRefs": references["check"]}},
        ]
        question = {"questionType": "short-answer", "questionKind": "short_answer", "answerMode": "short_answer",
                    "prompt": check["prompt"], "correctAnswers": [check["answer"]],
                    "answerSpec": {"answerType": "text", "expected": check["answer"]},
                    "knowledgePoint": chapter["title"], "subject": "math",
                    "evaluation": {"mode": "tutor"}, "requiredEvidenceRefs": references["check"]}
        return [_lesson(chapter, source, lesson_id=lesson_id, page=page, question=question, blocks=blocks)], []

    if chapter.get("subject") != "english":
        raise ChapterDraftValidationError("章节学科不受支持")
    questions = draft.get("questions")
    if not isinstance(questions, list) or len(questions) != 4:
        raise ChapterDraftValidationError("英语章节必须分别生成词义、指代、细节和推断题")
    by_kind: dict[str, dict[str, Any]] = {}
    for raw in questions:
        if not isinstance(raw, Mapping) or raw.get("kind") not in ENGLISH_KINDS or raw["kind"] in by_kind:
            raise ChapterDraftValidationError("英语题型缺失或重复")
        refs = _citations(raw.get("citations"), source)
        answer = _text(raw.get("answer"), "英语答案")
        variants = raw.get("teacherVariants")
        rubric = raw.get("rubric")
        if not isinstance(variants, list) or len(variants) > 12 or not isinstance(rubric, list) or not rubric:
            raise ChapterDraftValidationError("英语变体和评分标准必须显式提交供教师复核")
        by_kind[raw["kind"]] = {"prompt": _text(raw.get("prompt"), "英语题干"), "answer": answer,
                                "citations": refs, "variants": [_text(item, "英语答案变体", limit=300) for item in variants],
                                "rubric": [_text(item, "英语评分标准", limit=500) for item in rubric]}
    if set(by_kind) != ENGLISH_KINDS:
        raise ChapterDraftValidationError("英语题型必须覆盖四类阅读技能")
    lessons = []
    for kind, item in by_kind.items():
        referenced_pages = {reference["page"] for reference in item["citations"]}
        page = next(page for page in source["pages"] if page["page"] == min(referenced_pages))
        lesson_id = lesson_id_factory()
        question = {"questionType": "short-answer", "questionKind": kind, "answerMode": "short_answer", "subject": "english", "prompt": item["prompt"],
                    "acceptedAnswers": [item["answer"]], "correctAnswers": [item["answer"]],
                    "answerSpec": {"answerType": "text", "expected": item["answer"]},
                    "teacherVariants": item["variants"],
                    "variantReviewStatus": "needs_teacher_review", "rubric": {"supportStatus": "needs_review", "criteria": item["rubric"]},
                    "evaluation": {"mode": "tutor"}, "requiredEvidenceRefs": item["citations"]}
        reading_blocks = [
            {"id": f"{lesson_id}-reading-{source_page['page']}", "type": "markdown", "title": f"来源阅读 · 第 {source_page['page']} 页",
             "payload": {"markdown": source_page.get("text", ""), "sourceRefs": [reference for reference in item["citations"] if reference["page"] == source_page["page"]]}}
            for source_page in source["pages"] if source_page["page"] in referenced_pages
        ]
        blocks = [*reading_blocks,
                  {"id": f"{lesson_id}-quiz", "type": "quiz", "title": "阅读检查题", "payload": {"questionId": lesson_id, "prompt": item["prompt"], "sourceRefs": item["citations"]}}]
        lessons.append(_lesson(chapter, source, lesson_id=lesson_id, page=page, question=question, blocks=blocks))
    return lessons, []
