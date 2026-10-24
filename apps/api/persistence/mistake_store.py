"""Persistence for the mistake-coach domain.

The repository owns its table metadata so mistake work does not continue to
expand the application store. It shares the same SQLAlchemy engine and data
root with the rest of the application.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from domain.constants import DEMO_LEARNER_ID
from domain.questions.pipeline import replace_question_prompt

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
    # error_reason 保留为学生自评归因；AI 判断单独写入下方字段。
    Column("error_reason", String(32)),
    Column("ai_error_reason", String(32)),
    Column("ai_error_reason_confidence", Float),
    Column("notes", Text, nullable=False, default=""),
    Column("status", String(32), nullable=False, default="pending_confirmation"),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    Column("confirmed_at", Float),
)

Index("idx_mistake_items_learner", mistake_items.c.learner_id, mistake_items.c.updated_at.desc())
Index("idx_mistake_items_status", mistake_items.c.learner_id, mistake_items.c.status)

# The legacy columns above remain the API compatibility projection. New
# attributions are append-only so a later AI or teacher decision cannot erase
# the evidence that produced an earlier tutoring decision.
mistake_attributions = Table(
    "mistake_attributions",
    mistake_metadata,
    Column("attribution_id", String(64), primary_key=True),
    Column(
        "mistake_id",
        String(64),
        ForeignKey("mistake_items.mistake_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("source", String(16), nullable=False),
    Column("category", String(32), nullable=False),
    Column("confidence", Float, nullable=False),
    Column("evidence_json", json_document, nullable=False, default=dict),
    Column("model_version", String(96)),
    Column("created_at", Float, nullable=False),
    Column("accepted_at", Float),
    CheckConstraint(
        "source IN ('self', 'ai', 'teacher')",
        name="ck_mistake_attributions_source",
    ),
    CheckConstraint(
        "category <> ''",
        name="ck_mistake_attributions_category",
    ),
    CheckConstraint(
        "confidence >= 0 AND confidence <= 1",
        name="ck_mistake_attributions_confidence",
    ),
)

Index(
    "idx_mistake_attributions_mistake_created",
    mistake_attributions.c.mistake_id,
    mistake_attributions.c.created_at.desc(),
    mistake_attributions.c.attribution_id,
)
Index(
    "idx_mistake_attributions_source_category",
    mistake_attributions.c.source,
    mistake_attributions.c.category,
    mistake_attributions.c.created_at.desc(),
)


def _decode(value: Any) -> Any:
    if isinstance(value, str):
        import json
        return json.loads(value)
    return value


def _valid_mistake_id(value: str) -> bool:
    """Accept one opaque directory name without path or platform separators."""
    return bool(value) and value not in {".", ".."} and not any(
        separator in value for separator in ("/", "\\", "\x00")
    )


class MistakeStore:
    def __init__(
        self,
        *,
        engine: Engine | None = None,
        database_url: str | None = None,
        data_root: str | Path,
        file_assets: Any | None = None,
    ) -> None:
        if engine is None:
            if not database_url:
                raise ValueError("engine 或 database_url 必须提供一个")
            self.engine = create_engine(database_url, future=True)
        else:
            self.engine = engine
        self.root = Path(data_root).expanduser().resolve()
        self.mistake_root = self.root / "mistakes"
        self.mistake_root.mkdir(parents=True, exist_ok=True)
        self.file_assets = file_assets

    def _ensure_initialized(self) -> None:
        """Keep store call sites uniform; schema creation is Alembic-owned."""
        return None

    def item_directory(self, mistake_id: str) -> Path:
        if not _valid_mistake_id(mistake_id):
            raise ValueError("错题标识无效")
        if self.mistake_root.is_symlink():
            raise ValueError("错题数据根目录不能是符号链接")
        directory = self.mistake_root / mistake_id
        if directory.is_symlink():
            raise ValueError("错题目录不能是符号链接")
        directory.mkdir(parents=True, exist_ok=True)
        try:
            directory.resolve().relative_to(self.mistake_root.resolve())
        except ValueError as error:
            raise ValueError("错题目录超出受管目录") from error
        return directory

    def _stored_source_path(self, value: str, mistake_id: str) -> str:
        """Persist a data-root-relative source pointer, never a cache's absolute path."""
        if not value:
            return ""
        if not _valid_mistake_id(mistake_id):
            raise ValueError("错题标识无效")
        candidate = Path(value).expanduser()
        if ".." in candidate.parts:
            raise ValueError("错题原图路径不能包含父目录跳转")
        if not candidate.is_absolute():
            candidate = self.root / candidate
        candidate = Path(os.path.abspath(candidate))
        if self.mistake_root.is_symlink():
            raise ValueError("错题数据根目录不能是符号链接")
        root = self.mistake_root.resolve()
        try:
            relative = candidate.relative_to(root)
        except ValueError as error:
            raise ValueError("错题原图必须位于当前实例的错题数据目录内") from error
        if not relative.parts or relative.parts[0] != mistake_id:
            raise ValueError("错题原图与错题身份不匹配")
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("错题原图路径不能包含符号链接")
        return f"mistakes/{relative.as_posix()}"

    def _current_source_path(self, stored_path: str, mistake_id: str) -> str:
        """Project new relative pointers and legacy absolute rows into this cache."""
        if not stored_path:
            return ""
        if not _valid_mistake_id(mistake_id) or "\x00" in stored_path:
            return ""
        candidate = Path(stored_path).expanduser()
        if ".." in candidate.parts:
            return ""
        if candidate.is_absolute():
            parts = candidate.parts
            anchors = [index for index, part in enumerate(parts) if part == "mistakes"]
            if not anchors:
                return ""
            relative_parts = parts[anchors[-1] + 1:]
        else:
            raw_parts = tuple(part for part in stored_path.replace("\\", "/").split("/") if part)
            relative_parts = raw_parts[1:] if raw_parts and raw_parts[0] == "mistakes" else ()
        if not relative_parts or relative_parts[0] != mistake_id or any(part in {".", ".."} for part in relative_parts):
            return ""
        if self.mistake_root.is_symlink():
            return ""
        candidate = self.mistake_root.joinpath(*relative_parts)
        current = self.mistake_root
        for part in relative_parts:
            current = current / part
            if current.is_symlink():
                return ""
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.mistake_root.resolve())
        except ValueError:
            return ""
        return str(resolved)

    def _safe_local_path(self, path: Path, mistake_id: str) -> Path | None:
        """Reject symlinked parents and paths that leave this mistake's directory."""
        root = self.mistake_root
        if not _valid_mistake_id(mistake_id):
            return None
        if root.is_symlink():
            return None
        root_path = Path(os.path.abspath(root))
        candidate = Path(os.path.abspath(path.expanduser()))
        try:
            relative = candidate.relative_to(root_path)
        except ValueError:
            return None
        if not relative.parts or relative.parts[0] != mistake_id or any(part in {".", ".."} for part in relative.parts):
            return None
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return None
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            return None
        return candidate

    def persist_capture_files(self, learner_id: str, mistake_id: str, directory: Path) -> int:
        """Persist a capture's original image and OCR assets before acknowledging it."""
        if not _valid_mistake_id(mistake_id) or self.mistake_root.is_symlink():
            raise ValueError("错题目录身份无效")
        root = Path(os.path.abspath(self.mistake_root))
        candidate = Path(os.path.abspath(directory.expanduser()))
        try:
            relative = candidate.relative_to(root)
        except ValueError as error:
            raise ValueError("错题文件必须位于受管目录内") from error
        if (
            not relative.parts
            or relative.parts[0] != mistake_id
            or any(part in {".", ".."} for part in relative.parts)
        ):
            raise ValueError("错题目录与学生记录不匹配")
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("错题文件目录中出现符号链接")
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.mistake_root.resolve())
        except ValueError as error:
            raise ValueError("错题目录超出受管目录") from error
        files: list[Path] = []
        for path in sorted(resolved.rglob("*")):
            if path.is_symlink():
                raise ValueError("错题文件目录中出现符号链接")
            current = resolved
            for part in path.relative_to(resolved).parts:
                current = current / part
                if current.is_symlink():
                    raise ValueError("错题文件目录中出现符号链接")
            if path.is_file() and not path.name.endswith((".partial", ".part")):
                files.append(path)
        if self.file_assets is None or not getattr(self.file_assets, "enabled", False):
            return 0
        for path in files:
            self.file_assets.persist_mistake_file(learner_id, mistake_id, path)
        return len(files)

    def create(self, item: dict[str, Any]) -> dict[str, Any]:
        self._ensure_initialized()
        values = {
            "mistake_id": item["mistakeId"],
            "learner_id": item.get("learnerId", DEMO_LEARNER_ID),
            "source_filename": item["sourceFilename"],
            "content_type": item["contentType"],
            "source_image_path": self._stored_source_path(
                str(item.get("sourceImagePath") or ""), str(item["mistakeId"]),
            ),
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
            "ai_error_reason": item.get("aiErrorReason"),
            "ai_error_reason_confidence": item.get("aiErrorReasonConfidence"),
            "notes": item.get("notes", ""),
            "status": item.get("status", "pending_confirmation"),
            "created_at": item["createdAt"],
            "updated_at": item["updatedAt"],
            "confirmed_at": item.get("confirmedAt"),
        }
        with self.engine.begin() as connection:
            connection.execute(mistake_items.insert().values(**values))
        return self.get(item["mistakeId"]) or item

    def create_capture(self, item: dict[str, Any]) -> dict[str, Any]:
        """Create one capture row, returning the winner under concurrent retries."""
        created, _ = self.create_capture_with_status(item)
        return created

    def create_capture_with_status(self, item: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Create one capture row and report whether this call won the insert race."""
        try:
            return self.create(item), True
        except IntegrityError:
            existing = self.get(item["mistakeId"])
            if existing is not None:
                return existing, False
            raise

    def delete_capture(self, mistake_id: str, *, source_image_path: str) -> bool:
        """Remove only a newly-created capture row when its source still matches.

        Background cancellation may race the final import write. The source-path
        predicate prevents cleanup from deleting a later, unrelated record that
        reused the same stable capture-derived id.
        """
        self._ensure_initialized()
        portable_path = self._stored_source_path(source_image_path, mistake_id)
        with self.engine.begin() as connection:
            result = connection.execute(
                delete(mistake_items).where(
                    mistake_items.c.mistake_id == mistake_id,
                    mistake_items.c.source_image_path == portable_path,
                    mistake_items.c.status == "pending_confirmation",
                )
            )
        return bool(result.rowcount)

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
            # The published question already passed the teacher's release gate.
            # Accept its wording automatically, while leaving errorReason empty
            # so the tutor can ask for the learner's own explanation.
            "status": "unmastered",
            "created_at": timestamp,
            "updated_at": timestamp,
            "confirmed_at": None,
        }
        update_values = {
            "question_payload_json": values["question_payload_json"],
            "guide_cards_json": values["guide_cards_json"],
            "model_run_json": values["model_run_json"],
            "original_answer": answer_text,
            "chapter": values["chapter"],
            "knowledge_point": values["knowledge_point"],
            # Repeated offline delivery and a later re-error both point to the
            # same source question; objective evidence reopens it without
            # pretending the learner confirmed a cause or edited the prompt.
            "status": "unmastered",
            "updated_at": timestamp,
        }
        statement = postgresql_insert(mistake_items).values(**values).on_conflict_do_update(
            index_elements=[mistake_items.c.mistake_id],
            set_=update_values,
        )
        with self.engine.begin() as connection:
            connection.execute(statement)
        return self.get(mistake_id) or {
            "mistakeId": mistake_id,
            **values,
        }

    def activate_published_attempt(self, mistake_id: str, learner_id: str) -> dict[str, Any] | None:
        """Repair a legacy published-paper mistake on explicit learning start.

        Published source content is already quality-reviewed; this transition
        changes only its workflow state. It does not write ``confirmed_at`` or
        an error attribution, which remain learner-owned evidence.
        """
        self._ensure_initialized()
        current = self.get(mistake_id)
        if (
            not current
            or current.get("learnerId") != learner_id
            or current.get("contentType") != "application/vnd.dotty.publication+json"
        ):
            return None
        if current.get("status") != "pending_confirmation":
            return current
        with self.engine.begin() as connection:
            connection.execute(
                mistake_items.update()
                .where(
                    mistake_items.c.mistake_id == mistake_id,
                    mistake_items.c.learner_id == learner_id,
                    mistake_items.c.content_type == "application/vnd.dotty.publication+json",
                    mistake_items.c.status == "pending_confirmation",
                )
                .values(status="unmastered", updated_at=time.time())
            )
        return self.get(mistake_id)

    def get(self, mistake_id: str) -> dict[str, Any] | None:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(mistake_items).where(mistake_items.c.mistake_id == mistake_id)
            ).mappings().first()
        return self._serialize(row) if row else None

    def _insert_attribution(self, connection: Any, **values: Any) -> None:
        """Insert an attribution once by its stable PostgreSQL identifier."""
        statement = postgresql_insert(mistake_attributions).values(**values).on_conflict_do_nothing(
            index_elements=[mistake_attributions.c.attribution_id]
        )
        connection.execute(statement)

    def update_ai_error_reason(
        self,
        mistake_id: str,
        *,
        category: str,
        confidence: float,
        updated_at: float | None = None,
        evidence: dict[str, Any] | None = None,
        model_version: str | None = None,
    ) -> dict[str, Any] | None:
        """Persist an informative gated AI attribution without changing self-assessment.

        ``unknown`` is the normalization value for a model response without a
        usable category, so keeping it would make “not classified” look like a
        real historical attribution on the next turn.
        """
        self._ensure_initialized()
        current = self.get(mistake_id)
        if not current:
            return None
        if category == "unknown":
            return current
        timestamp = updated_at or time.time()
        with self.engine.begin() as connection:
            connection.execute(
                mistake_items.update()
                .where(mistake_items.c.mistake_id == mistake_id)
                .values(
                    ai_error_reason=category,
                    ai_error_reason_confidence=confidence,
                    updated_at=timestamp,
                )
            )
            self._insert_attribution(
                connection,
                attribution_id=hashlib.sha256(
                    f"ai:{mistake_id}:{category}:{timestamp}".encode("utf-8")
                ).hexdigest(),
                mistake_id=mistake_id,
                source="ai",
                category=category,
                confidence=max(0.0, min(1.0, confidence)),
                evidence_json=evidence or {"source": "tutoring"},
                model_version=model_version,
                created_at=timestamp,
                accepted_at=timestamp,
            )
        return self.get(mistake_id)

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
        question = payload["question"]
        replace_question_prompt(question, confirmation["prompt"])
        question["chapter"] = confirmation["chapter"]
        question["knowledgePoint"] = confirmation["knowledgePoint"]
        error_reason = (
            confirmation["errorReason"]
            if confirmation.get("errorReason") is not None
            else current.get("errorReason")
        )
        new_self_assessment = confirmation.get("errorReason")
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
                    # Omitted and explicit null both mean “keep the existing
                    # self-assessment”; only a concrete enum replaces it.
                    error_reason=error_reason,
                    notes=confirmation.get("notes", ""),
                    status="unmastered",
                    updated_at=timestamp,
                    confirmed_at=timestamp,
                )
            )
            if new_self_assessment:
                self._insert_attribution(
                    connection,
                    attribution_id=hashlib.sha256(
                        f"self:{mistake_id}:{new_self_assessment}:{timestamp}".encode("utf-8")
                    ).hexdigest(),
                    mistake_id=mistake_id,
                    source="self",
                    category=new_self_assessment,
                    confidence=1.0,
                    evidence_json={"source": "student-confirmation"},
                    model_version=None,
                    created_at=timestamp,
                    accepted_at=timestamp,
                )
        return self.get(mistake_id)

    def list_attributions(self, mistake_id: str) -> list[dict[str, Any]]:
        """Return attribution history in append order for internal consumers."""
        self._ensure_initialized()
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(mistake_attributions)
                .where(mistake_attributions.c.mistake_id == mistake_id)
                .order_by(
                    mistake_attributions.c.created_at,
                    mistake_attributions.c.attribution_id,
                )
            ).mappings().all()
        return [self._serialize_attribution(row) for row in rows]

    def latest_attribution(self, mistake_id: str) -> dict[str, Any] | None:
        """Return the latest attribution that has been explicitly accepted."""
        self._ensure_initialized()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(mistake_attributions)
                .where(
                    mistake_attributions.c.mistake_id == mistake_id,
                    mistake_attributions.c.accepted_at.is_not(None),
                )
                .order_by(
                    mistake_attributions.c.accepted_at.desc(),
                    mistake_attributions.c.created_at.desc(),
                    mistake_attributions.c.attribution_id.desc(),
                )
                .limit(1)
            ).mappings().first()
        return self._serialize_attribution(row) if row else None

    def set_archived(self, mistake_id: str, archived: bool) -> dict[str, Any] | None:
        self._ensure_initialized()
        current = self.get(mistake_id)
        if not current:
            return None
        trusted_publication = current.get("contentType") == "application/vnd.dotty.publication+json"
        next_status = "archived" if archived else (
            "unmastered" if trusted_publication or current["confirmedAt"] else "pending_confirmation"
        )
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
        path = self._safe_local_path(Path(stored_path), mistake_id)
        if path is None:
            return None
        if self.file_assets is not None and getattr(self.file_assets, "enabled", False):
            try:
                self.file_assets.restore_mistake_file(str(item["learnerId"]), mistake_id, path)
            except FileNotFoundError:
                return None
        return path if path.is_file() and not path.is_symlink() else None

    def asset_path(self, mistake_id: str, filename: str) -> Path | None:
        """Restore one OCR image only after the route has checked its learner owner."""
        if not filename or Path(filename).name != filename or "\\" in filename:
            return None
        item = self.get(mistake_id)
        source = self.source_path(mistake_id)
        if not item or not source:
            return None
        path = self._safe_local_path(source.parent / "assets" / filename, mistake_id)
        if path is None:
            return None
        if self.file_assets is not None and getattr(self.file_assets, "enabled", False):
            try:
                self.file_assets.restore_mistake_file(str(item["learnerId"]), mistake_id, path)
            except FileNotFoundError:
                return None
        return path if path.is_file() and not path.is_symlink() else None

    def _serialize(self, row: Any) -> dict[str, Any]:
        return {
            "mistakeId": row["mistake_id"],
            "learnerId": row["learner_id"],
            "sourceFilename": row["source_filename"],
            "contentType": row["content_type"],
            "sourceImagePath": self._current_source_path(
                str(row["source_image_path"] or ""), str(row["mistake_id"]),
            ),
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
            "aiErrorReason": row["ai_error_reason"],
            "aiErrorReasonConfidence": row["ai_error_reason_confidence"],
            "notes": row["notes"],
            "status": row["status"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "confirmedAt": row["confirmed_at"],
        }

    @staticmethod
    def _serialize_attribution(row: Any) -> dict[str, Any]:
        return {
            "attributionId": row["attribution_id"],
            "mistakeId": row["mistake_id"],
            "source": row["source"],
            "category": row["category"],
            "confidence": row["confidence"],
            "evidence": _decode(row["evidence_json"]),
            "modelVersion": row["model_version"],
            "createdAt": row["created_at"],
            "acceptedAt": row["accepted_at"],
        }

    def close(self) -> None:
        self.engine.dispose()
