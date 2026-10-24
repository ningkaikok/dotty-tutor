"""HTTP boundary for the student's read/start continuous-learning queue."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request

from application.services.continuous_learning import (
    ContinuousLearningConflict,
    continuous_learning_snapshot,
    start_continuous_learning,
)
from application.services.review_task_start import ReviewTaskStartConflict
from auth_context import learner_for_request
from domain.questions.student_view import student_review_task
from routers.review_routes import review_policy_view


def build_continuous_learning_router(
    *,
    assignment_store: Any,
    learning_store: Any,
    mistake_store: Any,
    review_store: Any,
    variation_service: Any,
) -> APIRouter:
    router = APIRouter(prefix="/api/learning", tags=["continuous-learning"])

    @router.get("/next")
    def get_next_learning_action(request: Request, learnerId: str | None = None) -> dict[str, Any]:
        """Read the next action without creating sessions, questions, or review tasks."""
        learner_id = learner_for_request(request, learnerId)
        return continuous_learning_snapshot(
            learner_id,
            assignment_store=assignment_store,
            learning_store=learning_store,
            mistake_store=mistake_store,
            review_store=review_store,
        )

    @router.post("/next/start")
    def start_next_learning_action(
        request: Request,
        learnerId: str | None = Body(default=None, embed=True),
    ) -> dict[str, Any]:
        """Apply an explicit start, then project a ready review without its answer key."""
        learner_id = learner_for_request(request, learnerId)
        try:
            snapshot, task = start_continuous_learning(
                learner_id,
                assignment_store=assignment_store,
                learning_store=learning_store,
                mistake_store=mistake_store,
                review_store=review_store,
                variation_service=variation_service,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (ReviewTaskStartConflict, ContinuousLearningConflict) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        if task is not None:
            mistake = mistake_store.get(task["mistakeId"])
            if mistake:
                task["mistake"] = {
                    "chapter": mistake.get("chapter", ""),
                    "knowledgePoint": mistake.get("knowledgePoint", ""),
                    "prompt": (mistake.get("questionPayload") or {}).get("question", {}).get("prompt", ""),
                }
            history = review_store.list_for_mistake(task["mistakeId"])
            projected = student_review_task(review_policy_view(task, history))
            projected["kind"] = "review"
            snapshot["next"] = projected
        return snapshot

    return router
