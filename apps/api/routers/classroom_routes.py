"""教师班级、作业指派和掌握度看板路由。"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from domain.contracts.classroom import AssignmentCreate, ClassCreate, ClassMemberCreate


def build_classroom_router(*, store: Any) -> APIRouter:
    router = APIRouter(prefix="/api")

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
            return store.create_assignment(
                assignment_id=uuid.uuid4().hex,
                class_id=class_id,
                publication_id=request.publicationId,
                title=request.title,
                due_at=request.dueAt,
                created_at=time.time(),
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get("/classes/{class_id}/dashboard")
    def class_dashboard(class_id: str, assignmentId: str | None = None) -> dict[str, Any]:
        try:
            return store.class_dashboard(class_id, assignment_id=assignmentId)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get("/assignments")
    def list_student_assignments(learnerId: str = "local-demo") -> dict[str, Any]:
        return {"learnerId": learnerId, "items": store.list_assignments_for_learner(learnerId)}

    return router
