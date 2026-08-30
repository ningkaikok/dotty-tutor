"""可编程课程、互动试卷作答与掌握度的 HTTP 路由。"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from domain.contracts.lesson import (
    ExerciseAttemptCreate,
    LearningSessionCreate,
    LearningSyncCreate,
    LessonDocument,
)
from observability import log_event


def build_learning_router(*, store: Any, mistake_store: Any | None = None) -> APIRouter:
    router = APIRouter(prefix="/api")

    def auto_record_mistake(
        session_id: str,
        request: ExerciseAttemptCreate,
        *,
        recorded_at: float,
    ) -> dict[str, Any] | None:
        """为已发布试卷的非正确作答建立错题记录，但不阻塞主学习日志。"""
        if mistake_store is None or request.assessment == "correct":
            return None
        session = store.get_learning_session(session_id)
        publication = store.load_publication(session["publicationId"]) if session else None
        if not session or not publication:
            return None
        lesson = next((
            item for item in publication.get("lessons", [])
            if (item.get("questionPayload") or {}).get("question", {}).get("id") == request.questionId
        ), None)
        if not lesson:
            log_event(
                "learning.mistake.skipped",
                level=30,
                session_id=session_id,
                question_id=request.questionId,
                reason="published question not found",
            )
            return None
        question = (lesson.get("questionPayload") or {}).get("question") or {}
        has_tutor_only_part = any(
            isinstance(part, dict)
            and isinstance(part.get("evaluation"), dict)
            and part["evaluation"].get("mode") == "tutor"
            for part in (question.get("subQuestions") or [])
        )
        # A tutor-only partial is an auditable completion, not an objective
        # wrong answer. Keep the attempt in the learning log but do not create
        # a misleading automatic mistake entry.
        if request.assessment == "partial" and has_tutor_only_part:
            return None
        mistake = mistake_store.record_published_attempt(
            learner_id=session["learnerId"],
            publication=publication,
            lesson=lesson,
            response=request.response,
            recorded_at=recorded_at,
        )
        log_event(
            "learning.mistake.auto_recorded",
            session_id=session_id,
            question_id=request.questionId,
            mistake_id=mistake["mistakeId"],
        )
        return mistake

    @router.post("/lessons")
    def save_lesson(document: LessonDocument) -> dict[str, Any]:
        saved = store.save_lesson(document.model_dump())
        log_event("lesson.saved", lesson_id=document.lessonId, version=document.version, status=document.status)
        return saved

    @router.get("/lessons/{lesson_id}")
    def get_lesson(lesson_id: str) -> dict[str, Any]:
        lesson = store.load_lesson(lesson_id)
        if not lesson:
            raise HTTPException(status_code=404, detail="课程不存在")
        return lesson

    @router.post("/learning/sessions")
    def create_learning_session(request: LearningSessionCreate) -> dict[str, Any]:
        publication = store.load_publication(request.publicationId)
        if not publication or publication["status"] != "published":
            # 只有通过发布质量门禁的内容才能创建真实学习记录；任意草稿 ID 不得污染掌握度数据。
            raise HTTPException(status_code=404, detail="已发布互动试卷不存在")
        session = store.create_learning_session(
            session_id=uuid.uuid4().hex,
            learner_id=request.learnerId,
            publication_id=request.publicationId,
            started_at=time.time(),
        )
        log_event(
            "learning.session.started",
            session_id=session["sessionId"],
            publication_id=request.publicationId,
        )
        return session

    @router.post("/learning/sessions/{session_id}/attempts")
    def record_exercise_attempt(session_id: str, request: ExerciseAttemptCreate) -> dict[str, Any]:
        received_at = time.time()
        try:
            result = store.record_exercise_attempt(
                attempt_id=request.attemptId or uuid.uuid4().hex,
                session_id=session_id,
                question_id=request.questionId,
                response=request.response,
                assessment=request.assessment,
                hint_level=request.hintLevel,
                duration_ms=request.durationMs,
                # 不接受客户端提供的未来时间；其余情况保留原始时间，使离线作答补传后顺序仍然真实。
                created_at=min(request.createdAt, received_at),
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        log_event(
            "learning.attempt.recorded",
            session_id=session_id,
            question_id=request.questionId,
            assessment=request.assessment,
            mastery_score=result["mastery"]["score"],
        )
        result["autoMistake"] = auto_record_mistake(session_id, request, recorded_at=received_at)
        return result

    @router.get("/learning/mastery/{learner_id}")
    def list_mastery(learner_id: str) -> dict[str, Any]:
        return {"learnerId": learner_id, "items": store.list_mastery(learner_id)}

    @router.get("/learning/sessions/{session_id}")
    def get_learning_session(session_id: str) -> dict[str, Any]:
        session = store.get_learning_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="学习会话不存在")
        return session

    @router.post("/learning/sessions/{session_id}/sync")
    def sync_learning_attempts(session_id: str, request: LearningSyncCreate) -> dict[str, Any]:
        """接收有上限的离线作答批次，并依靠 attemptId 安全幂等重试。"""
        synced: list[dict[str, Any]] = []
        for attempt in request.attempts:
            received_at = time.time()
            try:
                result = store.record_exercise_attempt(
                    attempt_id=attempt.attemptId or uuid.uuid4().hex,
                    session_id=session_id,
                    question_id=attempt.questionId,
                    response=attempt.response,
                    assessment=attempt.assessment,
                    hint_level=attempt.hintLevel,
                    duration_ms=attempt.durationMs,
                    created_at=min(attempt.createdAt, received_at),
                )
            except LookupError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            result["autoMistake"] = auto_record_mistake(session_id, attempt, recorded_at=received_at)
            synced.append(result)
        log_event(
            "learning.attempts.synced",
            session_id=session_id,
            attempt_count=len(synced),
        )
        return {"sessionId": session_id, "synced": synced}

    return router
