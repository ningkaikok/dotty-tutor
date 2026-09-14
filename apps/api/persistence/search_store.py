"""面向 Tutor 证据引用的 PostgreSQL 全文检索。"""

from __future__ import annotations

import re
import time
from typing import Any

from sqlalchemy import (
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.engine import Engine

metadata = MetaData()

lesson_search_documents = Table(
    "lesson_search_documents",
    metadata,
    Column("document_id", String(160), primary_key=True),
    Column("publication_id", String(64), nullable=False),
    Column("publication_version", Integer, nullable=False),
    Column("lesson_id", String(128), nullable=False),
    Column("question_id", String(128), nullable=False),
    Column("source_question_key", String(255), nullable=False),
    Column("source_pages_json", Text, nullable=False, default="[]"),
    Column("title", Text, nullable=False, default=""),
    Column("chapter", Text, nullable=False, default=""),
    Column("knowledge_points_json", Text, nullable=False, default="[]"),
    Column("body", Text, nullable=False, default=""),
    Column("search_text", Text, nullable=False, default=""),
    Column("search_vector", Text().with_variant(TSVECTOR(), "postgresql")),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

from sqlalchemy import Index  # noqa: E402

Index(
    "idx_lesson_search_documents_vector",
    lesson_search_documents.c.search_vector,
    postgresql_using="gin",
)
Index(
    "idx_lesson_search_documents_publication",
    lesson_search_documents.c.publication_id,
    lesson_search_documents.c.updated_at.desc(),
)

_CJK = re.compile(r"[\u3400-\u9fff]")


def cjk_bigrams(value: str) -> list[str]:
    """把连续中文切成二字词，避免 simple FTS 对中文整句不命中。"""
    chars = [char for char in value if _CJK.fullmatch(char)]
    return ["".join(chars[index:index + 2]) for index in range(max(0, len(chars) - 1))]


def build_search_text(*, title: str, chapter: str, knowledge_points: list[str], body: str) -> str:
    fields = [title, chapter, *knowledge_points, body]
    source = " ".join(item.strip() for item in fields if item and item.strip())
    return f"{source} {' '.join(cjk_bigrams(source))}".strip()


def _json_text(value: Any) -> str:
    if not value:
        return "[]"
    if isinstance(value, str):
        return value
    import json
    return json.dumps(value, ensure_ascii=False)


class TutorSearchStore:
    def __init__(self, *, engine: Engine) -> None:
        self.engine = engine

    def upsert_document(self, document: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        values = {
            "document_id": document["documentId"],
            "publication_id": document["publicationId"],
            "publication_version": int(document.get("publicationVersion") or 1),
            "lesson_id": document["lessonId"],
            "question_id": document["questionId"],
            "source_question_key": document.get("sourceQuestionKey") or document["questionId"],
            "source_pages_json": _json_text(document.get("sourcePages")),
            "title": document.get("title", ""),
            "chapter": document.get("chapter", ""),
            "knowledge_points_json": _json_text(document.get("knowledgePoints")),
            "body": document.get("body", ""),
            "search_text": build_search_text(
                title=document.get("title", ""),
                chapter=document.get("chapter", ""),
                knowledge_points=document.get("knowledgePoints") or [],
                body=document.get("body", ""),
            ),
            "created_at": now,
            "updated_at": now,
        }
        with self.engine.begin() as connection:
            existing = connection.execute(
                select(lesson_search_documents.c.document_id).where(
                    lesson_search_documents.c.document_id == values["document_id"]
                )
            ).first()
            if existing:
                connection.execute(
                    lesson_search_documents.update()
                    .where(lesson_search_documents.c.document_id == values["document_id"])
                    .values(**{key: value for key, value in values.items() if key not in {"document_id", "created_at"}})
                )
            else:
                connection.execute(lesson_search_documents.insert().values(**values))
            if self.engine.dialect.name == "postgresql":
                connection.execute(
                    lesson_search_documents.update()
                    .where(lesson_search_documents.c.document_id == values["document_id"])
                    .values(search_vector=func.to_tsvector("simple", lesson_search_documents.c.search_text))
                )
        return document

    def search(self, *, query: str, publication_id: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return []
        limit = max(1, min(limit, 20))
        with self.engine.connect() as connection:
            conditions = []
            if publication_id:
                conditions.append(lesson_search_documents.c.publication_id == publication_id)
            if self.engine.dialect.name == "postgresql":
                terms = " ".join(cjk_bigrams(query)) or query
                ts_query = func.plainto_tsquery("simple", terms)
                conditions.append(lesson_search_documents.c.search_vector.op("@@")(ts_query))
                statement = select(
                    lesson_search_documents,
                    func.ts_rank_cd(lesson_search_documents.c.search_vector, ts_query).label("rank"),
                ).where(*conditions).order_by(func.ts_rank_cd(lesson_search_documents.c.search_vector, ts_query).desc()).limit(limit)
            else:
                conditions.append(lesson_search_documents.c.search_text.ilike(f"%{query}%"))
                statement = select(lesson_search_documents).where(*conditions).order_by(lesson_search_documents.c.updated_at.desc()).limit(limit)
            rows = connection.execute(statement).mappings().all()
        return [self._result(row) for row in rows]

    def count(self, publication_id: str | None = None) -> int:
        with self.engine.connect() as connection:
            statement = select(func.count()).select_from(lesson_search_documents)
            if publication_id:
                statement = statement.where(lesson_search_documents.c.publication_id == publication_id)
            return int(connection.execute(statement).scalar() or 0)

    @staticmethod
    def _result(row: Any) -> dict[str, Any]:
        import json
        return {
            "documentId": row["document_id"],
            "publicationId": row["publication_id"],
            "publicationVersion": row["publication_version"],
            "lessonId": row["lesson_id"],
            "questionId": row["question_id"],
            "sourceQuestionKey": row["source_question_key"],
            "sourcePages": json.loads(row["source_pages_json"] or "[]"),
            "title": row["title"],
            "chapter": row["chapter"],
            "knowledgePoints": json.loads(row["knowledge_points_json"] or "[]"),
            "snippet": row["body"][:240],
            "rank": float(row.get("rank") or 0),
        }
