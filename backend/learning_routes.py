"""HTTP routes for programmable lessons, attempts and mastery state."""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from lesson_contracts import (
    ExerciseAttemptCreate,
    LearningSessionCreate,
    LearningSyncCreate,
    LessonDocument,
)
from observability import log_event


def build_learning_router(*, store: Any) -> APIRouter:
    router = APIRouter(prefix="/api")

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
        session = store.create_learning_session(
            session_id=uuid.uuid4().hex,
            learner_id=request.learnerId,
            lesson_id=request.lessonId,
            started_at=time.time(),
        )
        log_event("learning.session.started", session_id=session["sessionId"], lesson_id=request.lessonId)
        return session

    @router.post("/learning/sessions/{session_id}/attempts")
    def record_exercise_attempt(session_id: str, request: ExerciseAttemptCreate) -> dict[str, Any]:
        received_at = time.time()
        try:
            result = store.record_exercise_attempt(
                attempt_id=request.attemptId or uuid.uuid4().hex,
                session_id=session_id,
                question_id=request.questionId,
                knowledge_point=request.knowledgePoint,
                response=request.response,
                assessment=request.assessment,
                hint_level=request.hintLevel,
                duration_ms=request.durationMs,
                # Never accept a client timestamp in the future. The value is
                # otherwise preserved so offline attempts keep their ordering.
                created_at=min(request.createdAt, received_at) if request.createdAt is not None else received_at,
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
        """Accept a bounded batch so offline student answers can be retried safely."""
        synced: list[dict[str, Any]] = []
        for attempt in request.attempts:
            received_at = time.time()
            try:
                result = store.record_exercise_attempt(
                    attempt_id=attempt.attemptId or uuid.uuid4().hex,
                    session_id=session_id,
                    question_id=attempt.questionId,
                    knowledge_point=attempt.knowledgePoint,
                    response=attempt.response,
                    assessment=attempt.assessment,
                    hint_level=attempt.hintLevel,
                    duration_ms=attempt.durationMs,
                    created_at=min(attempt.createdAt, received_at) if attempt.createdAt is not None else received_at,
                )
            except LookupError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            synced.append(result)
        log_event(
            "learning.attempts.synced",
            session_id=session_id,
            attempt_count=len(synced),
        )
        return {"sessionId": session_id, "synced": synced}

    return router
