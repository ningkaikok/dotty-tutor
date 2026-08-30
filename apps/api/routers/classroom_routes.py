"""教师班级、作业指派和掌握度看板路由。"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from application.services.assignment_planning import AssignmentPlanningService
from domain.contracts.classroom import (
    AssignmentCreate,
    AssignmentPlanCreate,
    ClassCreate,
    ClassMemberCreate,
    TeacherReviewCreate,
)
from persistence.assignment_planning_store import AssignmentPlanningStore


def build_classroom_router(*, store: Any, planning_service: AssignmentPlanningService | None = None) -> APIRouter:
    router = APIRouter(prefix="/api")
    planner = planning_service or AssignmentPlanningService(
        store=store,
        planning_store=AssignmentPlanningStore(engine=store.engine),
    )

    @router.get("/classes")
    def list_classes() -> dict[str, Any]:
        return {"items": store.list_classes()}

    @router.post("/classes")
    def create_class(request: ClassCreate) -> dict[str, Any]:
        return store.create_class(
            class_id=uuid.uuid4().hex,
            name=request.name,
            subject=request.subject,
            grade_band=request.gradeBand,
            created_at=time.time(),
        )

    @router.get("/classes/{class_id}")
    def get_class(class_id: str) -> dict[str, Any]:
        result = store.get_class(class_id)
        if not result:
            raise HTTPException(status_code=404, detail="班级不存在")
        return result

    @router.post("/classes/{class_id}/members")
    def add_member(class_id: str, request: ClassMemberCreate) -> dict[str, Any]:
        try:
            return store.add_member(
                class_id=class_id,
                learner_id=request.learnerId,
                display_name=request.displayName,
                joined_at=time.time(),
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post("/classes/{class_id}/assignments")
    def create_assignment(class_id: str, request: AssignmentCreate) -> dict[str, Any]:
        try:
            existing_plan = planner.get_plan(class_id=class_id, plan_id=request.planId)
            if existing_plan and existing_plan["status"] == "confirmed" and existing_plan.get("assignmentId"):
                existing_assignment = store.get_assignment_by_plan(request.planId)
                if existing_assignment:
                    return existing_assignment
            current_fingerprint = planner.current_fingerprint(
                class_id=class_id, publication_id=request.publicationId,
            )
            planner.planning_store.confirm_and_create_assignment(
                plan_id=request.planId,
                class_id=class_id,
                publication_id=request.publicationId,
                title=request.title or "",
                due_at=request.dueAt,
                source_fingerprint=current_fingerprint,
                warning_confirmed=request.confirmWarnings,
                assignment_id=uuid.uuid4().hex,
                created_at=time.time(),
            )
            result = store.get_assignment_by_plan(request.planId)
            if not result:
                raise LookupError("作业创建结果不存在")
            return result
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post("/classes/{class_id}/assignment-plans")
    def create_assignment_plan(class_id: str, request: AssignmentPlanCreate) -> dict[str, Any]:
        try:
            return planner.create_plan(class_id=class_id, publication_id=request.publicationId)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get("/classes/{class_id}/assignment-plans/{plan_id}")
    def get_assignment_plan(class_id: str, plan_id: str) -> dict[str, Any]:
        result = planner.get_plan(class_id=class_id, plan_id=plan_id)
        if not result:
            raise HTTPException(status_code=404, detail="作业计划不存在")
        return result

    @router.get("/classes/{class_id}/dashboard")
    def class_dashboard(class_id: str, assignmentId: str | None = None) -> dict[str, Any]:
        try:
            return store.class_dashboard(class_id, assignment_id=assignmentId)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.post("/classes/{class_id}/assignments/{assignment_id}/reviews")
    def record_teacher_review(
        class_id: str,
        assignment_id: str,
        request: TeacherReviewCreate,
    ) -> dict[str, Any]:
        try:
            return store.record_teacher_review(
                event_id=uuid.uuid4().hex,
                class_id=class_id,
                assignment_id=assignment_id,
                learner_id=request.learnerId,
                question_id=request.questionId,
                knowledge_point_id=request.knowledgePointId,
                action=request.action,
                mastery_score=request.masteryScore,
                corrected_assessment=request.correctedAssessment,
                note=request.note,
                created_at=time.time(),
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/assignments")
    def list_student_assignments(learnerId: str = "local-demo") -> dict[str, Any]:
        return {"learnerId": learnerId, "items": store.list_assignments_for_learner(learnerId)}

    return router
