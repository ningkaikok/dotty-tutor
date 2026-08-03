"""HTTP routes for programmable lessons, attempts and mastery state."""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from lesson_contracts import ExerciseAttemptCreate, LearningSessionCreate, LessonDocument
from observability import log_event
from response_schemas import ExerciseAttemptResult, LearningSession, LessonDocumentResponse, MasteryListResponse


def build_learning_router(*, store: Any) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.post("/lessons", response_model=LessonDocumentResponse)
    def save_lesson(document: LessonDocument) -> dict[str, Any]:
        saved = store.save_lesson(document.model_dump())
        log_event("lesson.saved", lesson_id=document.lessonId, version=document.version, status=document.status)
        return saved

    @router.get("/lessons/{lesson_id}", response_model=LessonDocumentResponse)
    def get_lesson(lesson_id: str) -> dict[str, Any]:
        lesson = store.load_lesson(lesson_id)
        if not lesson:
            raise HTTPException(status_code=404, detail="课程不存在")
        return lesson

    @router.post("/learning/sessions", response_model=LearningSession)
    def create_learning_session(request: LearningSessionCreate) -> dict[str, Any]:
        session = store.create_learning_session(
            session_id=uuid.uuid4().hex,
            learner_id=request.learnerId,
            lesson_id=request.lessonId,
            started_at=time.time(),
        )
        log_event("learning.session.started", session_id=session["sessionId"], lesson_id=request.lessonId)
        return session

    @router.post("/learning/sessions/{session_id}/attempts", response_model=ExerciseAttemptResult)
    def record_exercise_attempt(session_id: str, request: ExerciseAttemptCreate) -> dict[str, Any]:
        try:
            result = store.record_exercise_attempt(
                attempt_id=uuid.uuid4().hex,
                session_id=session_id,
                question_id=request.questionId,
                knowledge_point=request.knowledgePoint,
                response=request.response,
                assessment=request.assessment,
                hint_level=request.hintLevel,
                duration_ms=request.durationMs,
                created_at=time.time(),
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

    @router.get("/learning/mastery/{learner_id}", response_model=MasteryListResponse)
    def list_mastery(learner_id: str) -> dict[str, Any]:
        return {"learnerId": learner_id, "items": store.list_mastery(learner_id)}

    return router
