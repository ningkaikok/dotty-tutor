"""课程、互动试卷、作答记录和知识点掌握度的持久化操作。"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import func, select

from domain.learning.mastery import (
    derive_mastery,
    knowledge_point_id,
    normalize_knowledge_point_name,
)
from persistence.base import DatabaseStore
from persistence.database import decode_json
from persistence.schema import (
    batch_questions,
    exercise_attempts,
    knowledge_points,
    learning_sessions,
    lesson_documents,
    lesson_publications,
    mastery_states,
    upload_jobs,
)
from publication_quality import PublicationQualityError


class LearningStore(DatabaseStore):
    """保存可编程课程和可追溯学习证据。

    页面状态不进入本 Store。课程文档、试卷版本、幂等作答和掌握度是数据库真相；临时选项、
    输入框内容和网络队列分别由前端管理。
    """

    def save_lesson(self, document: dict[str, Any]) -> dict[str, Any]:
        self._ensure_initialized()
        now = time.time()
        existing = self.load_lesson(document["lessonId"])
        values = {
            "lesson_id": document["lessonId"],
            "source_upload_id": document.get("sourceUploadId"),
            "title": document["title"],
            "version": document.get("version", 1),
            "status": document.get("status", "draft"),
            "knowledge_points_json": document.get("knowledgePoints", []),
            "blocks_json": document.get("blocks", []),
            "question_json": document.get("questionPayload", {}),
            "guide_cards_json": document.get("guideCards", []),
            "created_at": existing.get("createdAt", now) if existing else now,
            "updated_at": now,
        }
        with self.engine.begin() as connection:
            self._upsert(
                connection,
                lesson_documents,
                values,
                ["lesson_id"],
                [
                    "source_upload_id", "title", "version", "status",
                    "knowledge_points_json", "blocks_json", "question_json",
                    "guide_cards_json", "updated_at",
                ],
            )
        return self.load_lesson(document["lessonId"]) or document

    def load_lesson(self, lesson_id: str) -> dict[str, Any] | None:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(lesson_documents).where(lesson_documents.c.lesson_id == lesson_id)
            ).mappings().first()
        if not row:
            return None
        return {
            "lessonId": row["lesson_id"],
            "sourceUploadId": row["source_upload_id"],
            "title": row["title"],
            "version": row["version"],
            "status": row["status"],
            "knowledgePoints": decode_json(row["knowledge_points_json"]),
            "blocks": decode_json(row["blocks_json"]),
            "questionPayload": decode_json(row["question_json"]),
            "guideCards": decode_json(row["guide_cards_json"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def list_lessons(self, status: str | None = None) -> list[dict[str, Any]]:
        """按更新时间列出课程，供工作台和发布视图使用。"""
        self._ensure_initialized()
        query = select(lesson_documents).order_by(lesson_documents.c.updated_at.desc())
        if status:
            query = query.where(lesson_documents.c.status == status)
        with self.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [self._lesson_from_row(row) for row in rows]

    @staticmethod
    def _lesson_from_row(row: Any) -> dict[str, Any]:
        return {
            "lessonId": row["lesson_id"],
            "sourceUploadId": row["source_upload_id"],
            "title": row["title"],
            "version": row["version"],
            "status": row["status"],
            "knowledgePoints": decode_json(row["knowledge_points_json"]),
            "blocks": decode_json(row["blocks_json"]),
            "questionPayload": decode_json(row["question_json"]),
            "guideCards": decode_json(row["guide_cards_json"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def update_lesson_status(self, lesson_id: str, status: str) -> dict[str, Any] | None:
        """只改变课程发布状态，不重写题目正文和审核证据。"""
        self._ensure_initialized()
        with self.engine.begin() as connection:
            result = connection.execute(
                lesson_documents.update()
                .where(lesson_documents.c.lesson_id == lesson_id)
                .values(status=status, updated_at=time.time())
            )
            if not result.rowcount:
                return None
        return self.load_lesson(lesson_id)

    def create_publication(
        self,
        *,
        publication_id: str,
        title: str,
        source_upload_id: str | None,
        lesson_ids: list[str],
        status: str,
        created_at: float,
        version: int = 1,
        revision_of: str | None = None,
    ) -> dict[str, Any]:
        """创建一份由稳定 lesson ID 组成的不可变互动试卷版本。"""
        self._ensure_initialized()
        if len(lesson_ids) != len(set(lesson_ids)):
            raise ValueError("互动试卷不能包含重复题目")
        with self.engine.begin() as connection:
            rows = connection.execute(
                select(lesson_documents.c.lesson_id).where(
                    lesson_documents.c.lesson_id.in_(lesson_ids)
                )
            ).scalars().all()
            missing = sorted(set(lesson_ids) - set(rows))
            if missing:
                raise LookupError(f"课程不存在：{', '.join(missing[:3])}")
            connection.execute(lesson_publications.insert().values(
                publication_id=publication_id,
                title=title,
                source_upload_id=source_upload_id,
                lesson_ids_json=lesson_ids,
                status=status,
                version=version,
                revision_of=revision_of,
                created_at=created_at,
                updated_at=created_at,
            ))
        return self.load_publication(publication_id)  # type: ignore[return-value]

    def load_publication(self, publication_id: str) -> dict[str, Any] | None:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(lesson_publications).where(
                    lesson_publications.c.publication_id == publication_id
                )
            ).mappings().first()
            if not row:
                return None
            lesson_ids = decode_json(row["lesson_ids_json"])
            lessons = connection.execute(
                select(lesson_documents).where(
                    lesson_documents.c.lesson_id.in_(lesson_ids)
                )
            ).mappings().all()
        by_id = {lesson["lesson_id"]: self._lesson_from_row(lesson) for lesson in lessons}
        for lesson in by_id.values():
            source = next(item for item in lessons if item["lesson_id"] == lesson["lessonId"])
            lesson["knowledgePointId"] = knowledge_point_id(row["publication_id"], self._question_name(source))
        return {
            "publicationId": row["publication_id"],
            "title": row["title"],
            "sourceUploadId": row["source_upload_id"],
            "status": row["status"],
            "version": row["version"],
            "revisionOf": row["revision_of"],
            "lessonIds": lesson_ids,
            "lessons": [by_id[lesson_id] for lesson_id in lesson_ids if lesson_id in by_id],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def list_publications(self, status: str | None = None) -> list[dict[str, Any]]:
        self._ensure_initialized()
        query = select(lesson_publications).order_by(lesson_publications.c.updated_at.desc())
        if status:
            query = query.where(lesson_publications.c.status == status)
        with self.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [{
            "publicationId": row["publication_id"],
            "title": row["title"],
            "sourceUploadId": row["source_upload_id"],
            "status": row["status"],
            "version": row["version"],
            "revisionOf": row["revision_of"],
            "lessonIds": decode_json(row["lesson_ids_json"]),
            "lessonCount": len(decode_json(row["lesson_ids_json"])),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        } for row in rows]

    def update_publication_status(self, publication_id: str, status: str) -> dict[str, Any] | None:
        """只发布安全课程，并隔离自动重试后仍失败的候选题。

        生成阶段已对单题做有限重试；最终发布边界会移除残留坏题，使其余合格题可以发布。
        如果没有任何题通过，整份试卷仍以失败关闭，绝不发布空试卷或未验证题目。
        """
        self._ensure_initialized()
        recovery: dict[str, Any] | None = None
        with self.engine.begin() as connection:
            publication = connection.execute(
                select(lesson_publications).where(
                    lesson_publications.c.publication_id == publication_id
                )
            ).mappings().first()
            if not publication:
                return None
            current_status = publication["status"]
            allowed_transitions = {
                "draft": {"draft", "in_review", "archived"},
                "in_review": {"draft", "in_review", "published", "archived"},
                "published": {"published", "archived"},
                "archived": {"draft", "archived"},
            }
            if status not in allowed_transitions.get(current_status, set()):
                raise ValueError(f"发布状态不能从 {current_status} 直接变为 {status}")
            lesson_ids = decode_json(publication["lesson_ids_json"])
            lessons = connection.execute(
                select(lesson_documents).where(lesson_documents.c.lesson_id.in_(lesson_ids))
            ).mappings().all()
            if status == "published":
                lessons_by_id = {lesson["lesson_id"]: lesson for lesson in lessons}
                blockers: list[dict[str, Any]] = []
                ready_lesson_ids: list[str] = []
                for lesson_id in lesson_ids:
                    lesson = lessons_by_id.get(lesson_id)
                    if lesson is None:
                        blockers.append({"lessonId": lesson_id, "errors": ["课程记录不存在"]})
                        continue
                    quality = decode_json(lesson["question_json"]).get("quality", {})
                    if quality.get("status") == "ready":
                        ready_lesson_ids.append(lesson_id)
                    else:
                        blockers.append({
                            "lessonId": lesson_id,
                            "errors": list(quality.get("errors") or ["缺少质量校验结果"]),
                            "validatorVersion": quality.get("validatorVersion"),
                        })
                if not ready_lesson_ids:
                    raise PublicationQualityError(blockers)
                if blockers:
                    lesson_ids = ready_lesson_ids
                    recovery = {
                        "status": "recovered",
                        "publishedCount": len(ready_lesson_ids),
                        "quarantinedCount": len(blockers),
                        "quarantinedLessonIds": [item["lessonId"] for item in blockers],
                    }
                connection.execute(
                    lesson_documents.update()
                    .where(lesson_documents.c.lesson_id.in_(lesson_ids))
                    .values(status="published", updated_at=time.time())
                )
            connection.execute(
                lesson_publications.update()
                .where(lesson_publications.c.publication_id == publication_id)
                .values(
                    status=status,
                    lesson_ids_json=lesson_ids,
                    updated_at=time.time(),
                )
            )
        result = self.load_publication(publication_id)
        if result is not None and recovery is not None:
            result["qualityRecovery"] = recovery
        return result

    def create_learning_session(
        self,
        *,
        session_id: str,
        learner_id: str,
        publication_id: str,
        started_at: float,
    ) -> dict[str, Any]:
        self._ensure_initialized()
        with self.engine.begin() as connection:
            connection.execute(learning_sessions.insert().values(
                session_id=session_id,
                learner_id=learner_id,
                publication_id=publication_id,
                started_at=started_at,
                updated_at=started_at,
            ))
        return {
            "sessionId": session_id,
            "learnerId": learner_id,
            "publicationId": publication_id,
            "startedAt": started_at,
        }

    def record_exercise_attempt(
        self,
        *,
        attempt_id: str,
        session_id: str,
        question_id: str,
        knowledge_point: str | None = None,
        response: dict[str, Any],
        assessment: str,
        hint_level: int,
        duration_ms: int,
        created_at: float,
    ) -> dict[str, Any]:
        # Keep the optional argument for direct-store callers during the
        # transition, but never use client-provided text as identity data.
        self._ensure_initialized()
        with self.engine.begin() as connection:
            session = connection.execute(
                select(learning_sessions).where(
                    learning_sessions.c.session_id == session_id
                )
            ).mappings().first()
            if not session:
                raise LookupError("学习会话不存在")
            existing_attempt = connection.execute(
                select(exercise_attempts).where(exercise_attempts.c.attempt_id == attempt_id)
            ).mappings().first()
            if existing_attempt:
                if existing_attempt["session_id"] != session_id:
                    # attempt_id is the idempotency key. Reusing it in another
                    # session is a client error, not a successful retry.
                    raise LookupError("作答记录不属于当前学习会话")
                existing_kp_id = existing_attempt.get("knowledge_point_id")
                current = connection.execute(
                    select(mastery_states).where(
                        mastery_states.c.learner_id == session["learner_id"],
                        mastery_states.c.knowledge_point_id == existing_kp_id,
                    )
                ).mappings().first()
                return {
                    "attemptId": attempt_id,
                    "mastery": self._mastery_from_row(
                        current,
                        session["learner_id"],
                        existing_kp_id or knowledge_point_id(session["publication_id"], existing_attempt["knowledge_point"]),
                        existing_attempt.get("knowledge_point") or "未分类知识点",
                    ),
                }
            resolved = self._resolve_knowledge_point(
                connection,
                session=session,
                question_id=question_id,
                legacy_name=knowledge_point,
            )
            connection.execute(exercise_attempts.insert().values(
                attempt_id=attempt_id,
                session_id=session_id,
                publication_id=session["publication_id"],
                question_id=question_id,
                knowledge_point_id=resolved["knowledgePointId"],
                knowledge_point=resolved["name"],
                response_json=response,
                assessment=assessment,
                hint_level=hint_level,
                duration_ms=duration_ms,
                created_at=created_at,
            ))
            mastery = self._recompute_mastery(
                connection,
                learner_id=session["learner_id"],
                knowledge_point_id_value=resolved["knowledgePointId"],
                knowledge_point_name=resolved["name"],
            )
            connection.execute(
                learning_sessions.update()
                .where(learning_sessions.c.session_id == session_id)
                .values(updated_at=max(float(session["updated_at"]), created_at))
            )
        return {
            "attemptId": attempt_id,
            "mastery": mastery,
        }

    @staticmethod
    def _question_name(lesson: Any) -> str:
        payload = decode_json(lesson["question_json"]) or {}
        question = payload.get("question") or {}
        return normalize_knowledge_point_name(
            question.get("knowledgePoint")
            or (decode_json(lesson["knowledge_points_json"]) or [None])[0]
            or lesson["title"]
        )

    def _resolve_knowledge_point(
        self,
        connection: Any,
        *,
        session: Any,
        question_id: str,
        legacy_name: str | None,
    ) -> dict[str, str]:
        """Resolve the question from the immutable publication, never from client labels."""
        publication = connection.execute(
            select(lesson_publications).where(
                lesson_publications.c.publication_id == session["publication_id"]
            )
        ).mappings().first()
        if publication:
            lesson_ids = decode_json(publication["lesson_ids_json"]) or []
            lessons = connection.execute(
                select(lesson_documents).where(lesson_documents.c.lesson_id.in_(lesson_ids))
            ).mappings().all()
            lesson = next(
                (
                    item for item in lessons
                    if (decode_json(item["question_json"]) or {}).get("question", {}).get("id") == question_id
                ),
                None,
            )
            if lesson is None:
                raise LookupError("题目不属于当前已发布互动试卷")
            name = self._question_name(lesson)
            normalized = normalize_knowledge_point_name(name)
            point_id = knowledge_point_id(session["publication_id"], normalized)
            self._upsert(
                connection,
                knowledge_points,
                {
                    "knowledge_point_id": point_id,
                    "publication_id": session["publication_id"],
                    "name": name,
                    "normalized_name": normalized,
                    "created_at": time.time(),
                },
                ["knowledge_point_id"],
                ["name", "normalized_name"],
            )
            return {"knowledgePointId": point_id, "name": name}

        # Direct Store callers from before publication records existed may
        # still provide a legacy label. The HTTP route no longer accepts or
        # forwards that field, so untrusted clients cannot reach this path.
        if legacy_name:
            name = normalize_knowledge_point_name(legacy_name)
            return {
                "knowledgePointId": knowledge_point_id(session["publication_id"], name),
                "name": name,
            }
        raise LookupError("题目不属于当前已发布互动试卷")

    def _recompute_mastery(
        self,
        connection: Any,
        *,
        learner_id: str,
        knowledge_point_id_value: str,
        knowledge_point_name: str,
    ) -> dict[str, Any]:
        # The mastery row may not exist on the first attempt, so locking it
        # cannot serialize concurrent first writes. A published knowledge
        # point row is created before this method and is stable for the
        # publication; PostgreSQL locks it for the duration of this transaction
        # while SQLite treats the clause as a no-op.
        connection.execute(
            select(knowledge_points.c.knowledge_point_id)
            .where(knowledge_points.c.knowledge_point_id == knowledge_point_id_value)
            .with_for_update()
        ).first()
        rows = connection.execute(
            select(
                exercise_attempts.c.publication_id,
                exercise_attempts.c.question_id,
                exercise_attempts.c.attempt_id,
                exercise_attempts.c.assessment,
                exercise_attempts.c.created_at,
            )
            .select_from(exercise_attempts.join(
                learning_sessions,
                exercise_attempts.c.session_id == learning_sessions.c.session_id,
            ))
            .where(
                learning_sessions.c.learner_id == learner_id,
                exercise_attempts.c.knowledge_point_id == knowledge_point_id_value,
            )
        ).mappings().all()
        derived = derive_mastery(rows)
        computed_at = time.time()
        self._upsert(
            connection,
            mastery_states,
            {
                "learner_id": learner_id,
                "knowledge_point_id": knowledge_point_id_value,
                "knowledge_point": knowledge_point_name,
                "score": derived["score"],
                "raw_score": derived["raw_score"],
                "evidence_confidence": derived["evidence_confidence"],
                "evidence_count": derived["evidence_count"],
                "algorithm_version": derived["algorithm_version"],
                "computed_at": computed_at,
                "attempt_count": derived["attempt_count"],
                "correct_count": derived["correct_count"],
                "last_practiced_at": derived["last_practiced_at"],
            },
            ["learner_id", "knowledge_point_id"],
            [
                "knowledge_point", "score", "raw_score", "evidence_confidence",
                "evidence_count", "algorithm_version", "computed_at",
                "attempt_count", "correct_count", "last_practiced_at",
            ],
        )
        return {
            "learnerId": learner_id,
            "knowledgePointId": knowledge_point_id_value,
            "knowledgePoint": knowledge_point_name,
            "score": derived["score"],
            "rawScore": derived["raw_score"],
            "evidenceConfidence": derived["evidence_confidence"],
            "evidenceCount": derived["evidence_count"],
            "algorithmVersion": derived["algorithm_version"],
            "computedAt": computed_at,
            "attemptCount": derived["attempt_count"],
            "correctCount": derived["correct_count"],
            "lastPracticedAt": derived["last_practiced_at"],
        }

    def get_learning_session(self, session_id: str) -> dict[str, Any] | None:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            session = connection.execute(
                select(learning_sessions).where(learning_sessions.c.session_id == session_id)
            ).mappings().first()
            if not session:
                return None
            attempts = connection.execute(
                select(exercise_attempts)
                .where(exercise_attempts.c.session_id == session_id)
                .order_by(exercise_attempts.c.created_at.asc(), exercise_attempts.c.attempt_id.asc())
            ).mappings().all()
        return {
            "sessionId": session["session_id"],
            "learnerId": session["learner_id"],
            "publicationId": session["publication_id"],
            "startedAt": session["started_at"],
            "updatedAt": session["updated_at"],
            "attempts": [{
                "attemptId": row["attempt_id"],
                "questionId": row["question_id"],
                "knowledgePointId": row.get("knowledge_point_id"),
                "knowledgePoint": row.get("knowledge_point"),
                "response": decode_json(row["response_json"]),
                "assessment": row["assessment"],
                "hintLevel": row["hint_level"],
                "durationMs": row["duration_ms"],
                "createdAt": row["created_at"],
            } for row in attempts],
        }

    @staticmethod
    def _mastery_from_row(row: Any, learner_id: str, knowledge_point_id_value: str, knowledge_point: str) -> dict[str, Any]:
        return {
            "learnerId": learner_id,
            "knowledgePointId": knowledge_point_id_value,
            "knowledgePoint": knowledge_point,
            "score": float(row["score"]) if row else 0.0,
            "rawScore": float(row["raw_score"]) if row and row.get("raw_score") is not None else 0.0,
            "evidenceConfidence": float(row["evidence_confidence"]) if row and row.get("evidence_confidence") is not None else 0.0,
            "evidenceCount": int(row["evidence_count"]) if row and row.get("evidence_count") is not None else 0,
            "algorithmVersion": row.get("algorithm_version") if row else "mastery-v2",
            "computedAt": row.get("computed_at") if row else None,
            "attemptCount": int(row["attempt_count"]) if row else 0,
            "correctCount": int(row["correct_count"]) if row else 0,
            "lastPracticedAt": row["last_practiced_at"] if row else None,
        }

    def list_mastery(self, learner_id: str) -> list[dict[str, Any]]:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(mastery_states)
                .where(mastery_states.c.learner_id == learner_id)
                .order_by(mastery_states.c.last_practiced_at.desc())
            ).mappings().all()
        return [{
            "learnerId": row["learner_id"],
            "knowledgePointId": row.get("knowledge_point_id") or knowledge_point_id("legacy", row.get("knowledge_point") or "未分类知识点"),
            "knowledgePoint": row.get("knowledge_point") or "未分类知识点",
            "score": row["score"],
            "rawScore": row.get("raw_score", row["score"]),
            "evidenceConfidence": row.get("evidence_confidence", 0.0),
            "evidenceCount": row.get("evidence_count", row.get("attempt_count", 0)),
            "algorithmVersion": row.get("algorithm_version", "mastery-v1-legacy"),
            "computedAt": row.get("computed_at"),
            "attemptCount": row["attempt_count"],
            "correctCount": row["correct_count"],
            "lastPracticedAt": row["last_practiced_at"],
        } for row in rows]

    def counts(self) -> dict[str, int]:
        """Return row counts for migration verification and diagnostics."""
        self._ensure_initialized()
        with self.engine.connect() as connection:
            return {
                "upload_jobs": connection.scalar(
                    select(func.count()).select_from(upload_jobs)
                ) or 0,
                "batch_questions": connection.scalar(
                    select(func.count()).select_from(batch_questions)
                ) or 0,
                "lesson_documents": connection.scalar(
                    select(func.count()).select_from(lesson_documents)
                ) or 0,
                "lesson_publications": connection.scalar(
                    select(func.count()).select_from(lesson_publications)
                ) or 0,
                "learning_sessions": connection.scalar(
                    select(func.count()).select_from(learning_sessions)
                ) or 0,
                "exercise_attempts": connection.scalar(
                    select(func.count()).select_from(exercise_attempts)
                ) or 0,
                "knowledge_points": connection.scalar(
                    select(func.count()).select_from(knowledge_points)
                ) or 0,
                "mastery_states": connection.scalar(
                    select(func.count()).select_from(mastery_states)
                ) or 0,
            }
