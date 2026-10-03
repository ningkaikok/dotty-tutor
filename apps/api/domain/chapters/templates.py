"""Pure construction of source-bound chapter lesson documents."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from typing import Any

from domain.contracts.lesson import lesson_document_from_payload

_ENGLISH_PLACE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bmoved to\s+([A-Z][a-z]+)\b"), "According to the passage, where did the person move?"),
    (re.compile(r"\blives? in\s+([A-Z][a-z]+)\b"), "According to the passage, where does the person live?"),
    (re.compile(r"\bwas born in\s+([A-Z][a-z]+)\b"), "According to the passage, where was the person born?"),
)


def _lesson_id() -> str:
    return uuid.uuid4().hex


def build_chapter_lessons(
    chapter: dict[str, Any],
    source: dict[str, Any],
    *,
    lesson_id_factory: Callable[[], str] = _lesson_id,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Construct lesson documents and authoring issues without persistence.

    ``lesson_id_factory`` keeps IDs injectable for deterministic callers while
    preserving the service's existing random IDs by default.
    """
    revision_id = source["sourceRevisionId"]
    issues = list(source.get("issues") or [])
    documents: list[dict[str, Any]] = []

    for page in source["pages"]:
        lesson_id = lesson_id_factory()
        text = page["text"]
        locator = {
            "sourceRevisionId": revision_id,
            "page": page["page"],
            "regions": page["regions"],
        }
        answer: str | None = None
        prompt = "请先补充一道可由该来源核对的检查题与答案。"
        if chapter["subject"] == "math":
            formula = re.search(r"[^。；;\n]{0,40}=[^。；;\n]{1,60}", text)
            if formula:
                answer = formula.group(0).strip()
                prompt = "写出来源标注的关系式。"
        elif chapter["subject"] == "english":
            for pattern, matched_prompt in _ENGLISH_PLACE_PATTERNS:
                match = pattern.search(text)
                if match:
                    answer = match.group(1)
                    prompt = matched_prompt
                    break

        page_sentences = page.get("sentences") or []
        page_sentence = next(
            (
                sentence for sentence in page_sentences
                if sentence["text"] in text
                and (chapter["subject"] != "english" or answer is None or answer in sentence["text"])
            ),
            page_sentences[0] if page_sentences else None,
        )
        evidence_refs = (
            [{
                "sourceRevisionId": revision_id,
                "page": page["page"],
                "sentenceId": page_sentence["sentenceId"],
                "quote": page_sentence["text"],
            }]
            if page_sentence else [{"sourceRevisionId": revision_id, "page": page["page"]}]
        )
        question: dict[str, Any] = {
            "id": lesson_id,
            "questionType": "short-answer",
            "prompt": prompt,
            "knowledgePoint": chapter["title"],
            "sourceRevisionId": revision_id,
            "sourceLocator": locator,
            "requiredEvidenceRefs": evidence_refs,
            "subject": chapter["subject"],
        }
        if answer:
            if chapter["subject"] == "english":
                question.update({
                    "questionKind": "explicit",
                    "answerMode": "objective",
                    "acceptedAnswers": [answer],
                    "rubric": {"supportStatus": "supported"},
                })
            else:
                question["evaluation"] = {"mode": "deterministic"}
                question["correctAnswers"] = [answer]
        else:
            question["evaluation"] = {"mode": "tutor"}
            issues.append({
                "code": "quiz_needs_authoring",
                "lessonId": lesson_id,
                "message": "现有来源不足以确定客观答案，需要教师补充题目与评分依据",
            })

        payload = {
            "question": question,
            "lessonSteps": [],
            "quality": {
                "status": "ready" if answer else "needs_review",
                "errors": [] if answer else ["缺少可验证的标准答案"],
            },
        }
        document = lesson_document_from_payload(payload, source_upload_id=source.get("uploadId"))
        document.update({
            "lessonId": lesson_id,
            "title": f"{chapter['title']} · 第 {page['page']} 页",
            "version": chapter["version"],
            "status": "in_review",
            "knowledgePoints": [chapter["title"]],
            "blocks": [
                {
                    "id": f"{lesson_id}-concept", "type": "markdown", "title": "概念",
                    "payload": {"markdown": text, "text": text, "sourceLocator": locator},
                },
                {
                    "id": f"{lesson_id}-example", "type": "annotation", "title": "来源例句/例式",
                    "payload": {"text": text, "sourceLocator": locator},
                },
                {
                    "id": f"{lesson_id}-hint", "type": "hint", "title": "提示",
                    "payload": {
                        "level": 1,
                        "hint": "回到标注页，找出与问题直接相关的原文或关系式。",
                        "sourceLocator": locator,
                    },
                },
                {
                    "id": f"{lesson_id}-quiz", "type": "quiz", "title": "检查题",
                    "payload": {"questionId": lesson_id},
                },
            ],
            "questionPayload": payload,
            "sourceRevisionId": revision_id,
            "sourceLocator": locator,
            "reviewIssues": [],
        })
        documents.append(document)

    return documents, issues
