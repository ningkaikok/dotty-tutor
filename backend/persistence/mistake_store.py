"""Persistence for the mistake-coach domain.

The repository owns its table metadata so mistake work does not continue to
expand the general TutorStore. It shares the same SQLAlchemy engine and data
root with the rest of the application.
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from typing import Any

from sqlalchemy import Column, Float, Index, JSON, MetaData, String, Table, Text, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine


mistake_metadata = MetaData()
json_document = JSON().with_variant(JSONB(), "postgresql")

mistake_items = Table(
    "mistake_items",
    mistake_metadata,
    Column("mistake_id", String(64), primary_key=True),
    Column("learner_id", String(128), nullable=False),
    Column("source_filename", Text, nullable=False),
    Column("content_type", String(255), nullable=False),
    Column("source_image_path", Text, nullable=False),
    Column("source_image_url", Text, nullable=False),
    Column("question_payload_json", json_document, nullable=False),
    Column("guide_cards_json", json_document, nullable=False, default=list),
    Column("ocr_run_json", json_document, nullable=False, default=dict),
    Column("model_run_json", json_document, nullable=False, default=dict),
    Column("original_answer", Text, nullable=False, default=""),
    Column("subject", String(80), nullable=False, default="数学"),
    Column("grade_band", String(80), nullable=False, default="初中"),
    Column("chapter", Text, nullable=False),
    Column("knowledge_point", Text, nullable=False),
    Column("error_reason", String(32)),
    Column("notes", Text, nullable=False, default=""),
    Column("status", String(32), nullable=False, default="pending_confirmation"),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    Column("confirmed_at", Float),
)

Index("idx_mistake_items_learner", mistake_items.c.learner_id, mistake_items.c.updated_at.desc())
Index("idx_mistake_items_status", mistake_items.c.learner_id, mistake_items.c.status)


def _decode(value: Any) -> Any:
    if isinstance(value, str):
        import json
        return json.loads(value)
    return value


class MistakeStore:
    def __init__(
        self,
        *,
        engine: Engine | None = None,
        database_url: str | None = None,
        data_root: str | Path,
    ) -> None:
        if engine is None and not database_url:
            raise ValueError("engine 或 database_url 必须提供一个")
        self.engine = engine if engine is not None else create_engine(database_url, future=True)
        self.root = Path(data_root).expanduser().resolve()
        self.mistake_root = self.root / "mistakes"
        self.mistake_root.mkdir(parents=True, exist_ok=True)
        self._initialized = False
        self._initialize_lock = threading.Lock()

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            mistake_metadata.create_all(self.engine)
            self._initialized = True

    def item_directory(self, mistake_id: str) -> Path:
        directory = self.mistake_root / mistake_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def create(self, item: dict[str, Any]) -> dict[str, Any]:
        self._ensure_initialized()
        values = {
            "mistake_id": item["mistakeId"],
            "learner_id": item.get("learnerId", "local-demo"),
            "source_filename": item["sourceFilename"],
            "content_type": item["contentType"],
            "source_image_path": item["sourceImagePath"],
            "source_image_url": item["sourceImageUrl"],
            "question_payload_json": item["questionPayload"],
            "guide_cards_json": item.get("guideCards", []),
            "ocr_run_json": item.get("ocrRun", {}),
            "model_run_json": item.get("modelRun", {}),
            "original_answer": item.get("originalAnswer", ""),
            "subject": item.get("subject", "数学"),
            "grade_band": item.get("gradeBand", "初中"),
            "chapter": item["chapter"],
            "knowledge_point": item["knowledgePoint"],
            "error_reason": item.get("errorReason"),
            "notes": item.get("notes", ""),
            "status": item.get("status", "pending_confirmation"),
            "created_at": item["createdAt"],
            "updated_at": item["updatedAt"],
            "confirmed_at": item.get("confirmedAt"),
        }
        with self.engine.begin() as connection:
            connection.execute(mistake_items.insert().values(**values))
        return self.get(item["mistakeId"]) or item

    def record_published_attempt(
        self,
        *,
        learner_id: str,
        publication: dict[str, Any],
        lesson: dict[str, Any],
        response: dict[str, Any],
        recorded_at: float | None = None,
    ) -> dict[str, Any]:
        """把互动试卷中的错答幂等写入个人错题本。

        已发布试卷已经通过内容质量门禁，因此不需要再次进入 OCR 人工确认流程。稳定 ID
        由“学生 + 试卷 + 题目”生成：离线补传或网络重试只会更新同一条错题，不会重复计入。
        """
        self._ensure_initialized()
        timestamp = recorded_at or time.time()
        payload = lesson.get("questionPayload") or {}
        question = payload.get("question") or {}
        question_id = str(question.get("id") or lesson.get("lessonId") or "unknown")
        identity = f"{learner_id}:{publication['publicationId']}:{question_id}".encode("utf-8")
        mistake_id = f"paper-{hashlib.sha256(identity).hexdigest()[:32]}"
        answer_text = str(response.get("text") or "")
        values = {
            "mistake_id": mistake_id,
            "learner_id": learner_id,
            "source_filename": publication.get("title") or "互动试卷",
            "content_type": "application/vnd.dotty.publication+json",
            "source_image_path": "",
            "source_image_url": "",
            "question_payload_json": payload,
            "guide_cards_json": lesson.get("guideCards") or [],
            "ocr_run_json": payload.get("ocrRun") or {},
            "model_run_json": payload.get("modelRun") or {},
            "original_answer": answer_text,
            "subject": "数学",
            "grade_band": "初中",
            "chapter": str(question.get("chapter") or "未归类章节"),
            "knowledge_point": str(question.get("knowledgePoint") or "未归类知识点"),
            "error_reason": None,
            "notes": "由互动试卷答错后自动记录",
            "status": "unmastered",
            "created_at": timestamp,
            "updated_at": timestamp,
            "confirmed_at": timestamp,
        }
        update_values = {
            "question_payload_json": values["question_payload_json"],
            "guide_cards_json": values["guide_cards_json"],
            "model_run_json": values["model_run_json"],
            "original_answer": answer_text,
            "chapter": values["chapter"],
            "knowledge_point": values["knowledge_point"],
            # 学生再次做错已经掌握的题时，应重新回到待掌握状态。
            "status": "unmastered",
            "updated_at": timestamp,
            "confirmed_at": timestamp,
        }
        if self.engine.dialect.name == "postgresql":
            statement = postgresql_insert(mistake_items).values(**values).on_conflict_do_update(
                index_elements=[mistake_items.c.mistake_id],
                set_=update_values,
            )
        else:
            statement = sqlite_insert(mistake_items).values(**values).on_conflict_do_update(
                index_elements=[mistake_items.c.mistake_id],
                set_=update_values,
            )
        with self.engine.begin() as connection:
            connection.execute(statement)
        return self.get(mistake_id) or {
            "mistakeId": mistake_id,
            **values,
        }

    def get(self, mistake_id: str) -> dict[str, Any] | None:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(mistake_items).where(mistake_items.c.mistake_id == mistake_id)
            ).mappings().first()
        return self._serialize(row) if row else None

    def list(self, learner_id: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
        self._ensure_initialized()
        query = select(mistake_items).where(mistake_items.c.learner_id == learner_id)
        if not include_archived:
            query = query.where(mistake_items.c.status != "archived")
        query = query.order_by(mistake_items.c.updated_at.desc())
        with self.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [self._serialize(row) for row in rows]

    def confirm(self, mistake_id: str, confirmation: dict[str, Any], *, confirmed_at: float | None = None) -> dict[str, Any] | None:
        self._ensure_initialized()
        current = self.get(mistake_id)
        if not current:
            return None
        timestamp = confirmed_at or time.time()
        payload = current["questionPayload"]
        payload["question"] = {
            **payload.get("question", {}),
            "prompt": confirmation["prompt"],
            "chapter": confirmation["chapter"],
            "knowledgePoint": confirmation["knowledgePoint"],
        }
        # 学生修正后的题干成为权威值。旧 contentBlocks 可能仍包含修正前 OCR 文本，
        # 因此清除它，强制后续渲染器根据新题干/选项重建或回退显示。
        payload["question"].pop("contentBlocks", None)
        with self.engine.begin() as connection:
            connection.execute(
                mistake_items.update()
                .where(mistake_items.c.mistake_id == mistake_id)
                .values(
                    question_payload_json=payload,
                    original_answer=confirmation.get("originalAnswer", ""),
                    subject=confirmation.get("subject", "数学"),
                    grade_band=confirmation.get("gradeBand", "初中"),
                    chapter=confirmation["chapter"],
                    knowledge_point=confirmation["knowledgePoint"],
                    error_reason=confirmation["errorReason"],
                    notes=confirmation.get("notes", ""),
                    status="unmastered",
                    updated_at=timestamp,
                    confirmed_at=timestamp,
                )
            )
        return self.get(mistake_id)

    def set_archived(self, mistake_id: str, archived: bool) -> dict[str, Any] | None:
        self._ensure_initialized()
        current = self.get(mistake_id)
        if not current:
            return None
        next_status = "archived" if archived else ("unmastered" if current["confirmedAt"] else "pending_confirmation")
        with self.engine.begin() as connection:
            connection.execute(
                mistake_items.update()
                .where(mistake_items.c.mistake_id == mistake_id)
                .values(status=next_status, updated_at=time.time())
            )
        return self.get(mistake_id)

    def mark_mastered(self, mistake_id: str) -> dict[str, Any] | None:
        """Promote a confirmed mistake after objective validation succeeds.

        The store does not decide *when* a learner has mastered a question;
        that policy is derived from immutable variation attempts in the
        practice route. Keeping the transition here makes the write explicit.
        """
        self._ensure_initialized()
        current = self.get(mistake_id)
        if not current:
            return None
        if current["status"] == "mastered":
            return current
        if current["status"] != "unmastered":
            return None
        with self.engine.begin() as connection:
            connection.execute(
                mistake_items.update()
                .where(
                    mistake_items.c.mistake_id == mistake_id,
                    mistake_items.c.status == "unmastered",
                )
                .values(status="mastered", updated_at=time.time())
            )
        return self.get(mistake_id)

    def source_path(self, mistake_id: str) -> Path | None:
        item = self.get(mistake_id)
        if not item:
            return None
        stored_path = str(item.get("sourceImagePath") or "")
        if not stored_path:
            return None
        path = Path(stored_path).expanduser().resolve()
        expected_root = self.mistake_root.resolve()
        if expected_root in path.parents and path.is_file():
            return path

        # 数据库可能从宿主机迁移到 Docker，旧记录里的绝对路径会失效；
        # 文件仍在同一个数据卷时，按错题 ID 和原文件名重新定位，避免前端只看到破图。
        filename = Path(stored_path).name
        if not filename or filename in {".", ".."}:
            return None
        relocated = self.mistake_root / mistake_id / filename
        return relocated if relocated.is_file() else None

    @staticmethod
    def _serialize(row: Any) -> dict[str, Any]:
        return {
            "mistakeId": row["mistake_id"],
            "learnerId": row["learner_id"],
            "sourceFilename": row["source_filename"],
            "contentType": row["content_type"],
            "sourceImagePath": row["source_image_path"],
            "sourceImageUrl": row["source_image_url"],
            "questionPayload": _decode(row["question_payload_json"]),
            "guideCards": _decode(row["guide_cards_json"]),
            "ocrRun": _decode(row["ocr_run_json"]),
            "modelRun": _decode(row["model_run_json"]),
            "originalAnswer": row["original_answer"],
            "subject": row["subject"],
            "gradeBand": row["grade_band"],
            "chapter": row["chapter"],
            "knowledgePoint": row["knowledge_point"],
            "errorReason": row["error_reason"],
            "notes": row["notes"],
            "status": row["status"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "confirmedAt": row["confirmed_at"],
        }

    def close(self) -> None:
        self.engine.dispose()
