"""从已发布互动试卷建立 Tutor 可引用的检索文档。"""

from __future__ import annotations

import hashlib
from typing import Any


class TutorSearchService:
    def __init__(self, *, search_store: Any) -> None:
        self.search_store = search_store

    def rebuild_publication(self, publication: dict[str, Any]) -> int:
        if publication.get("status") != "published":
            raise ValueError("只有已发布互动试卷可以建立检索索引")
        count = 0
        for lesson in publication.get("lessons") or []:
            question = lesson.get("questionPayload", {}).get("question", {})
            question_id = str(question.get("id") or lesson.get("lessonId") or "")
            if not question_id:
                continue
            body = " ".join([
                str(question.get("prompt") or ""),
                " ".join(str(item) for item in question.get("givens") or []),
                str(lesson.get("title") or ""),
            ]).strip()
            document_id = hashlib.sha256(
                f"{publication['publicationId']}:{lesson.get('lessonId')}:{question_id}".encode("utf-8")
            ).hexdigest()[:64]
            self.search_store.upsert_document({
                "documentId": document_id,
                "publicationId": publication["publicationId"],
                "publicationVersion": publication.get("version", 1),
                "lessonId": lesson.get("lessonId", question_id),
                "questionId": question_id,
                "sourceQuestionKey": question_id,
                "sourcePages": [],
                "title": lesson.get("title", ""),
                "chapter": lesson.get("chapter", ""),
                "knowledgePoints": lesson.get("knowledgePoints") or [],
                "body": body,
            })
            count += 1
        return count
