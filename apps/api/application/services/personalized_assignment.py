"""Generate one privacy-bounded, class-level personalized assignment."""

from __future__ import annotations

import time
import uuid
from typing import Any

from application.services.lesson_generation import (
    PersonalizedLessonGenerationError,
    generate_personalized_lessons,
)


class PersonalizedAssignmentError(ValueError):
    """A personalized assignment was rejected before it became assignable."""


class PersonalizedAssignmentService:
    """Orchestrate planning evidence, one model call, and publication gates."""

    def __init__(self, *, store: Any, planning_service: Any, model_runtime: Any) -> None:
        self.store = store
        self.planning_service = planning_service
        self.model_runtime = model_runtime

    def generate(self, *, class_id: str, plan_id: str, question_count: int) -> dict[str, Any]:
        source_plan, context = self.planning_service.personalized_context(
            class_id=class_id, plan_id=plan_id,
        )
        prior_id = (source_plan.get("result") or {}).get("personalizedFinalPlanId")
        if prior_id:
            prior = self.planning_service.get_plan(class_id=class_id, plan_id=prior_id)
            if prior:
                return prior
        try:
            lessons = generate_personalized_lessons(
                context, question_count, model_runtime=self.model_runtime,
            )
        except PersonalizedLessonGenerationError as error:
            raise PersonalizedAssignmentError(str(error)) from error
        if len(lessons) != question_count:
            raise PersonalizedAssignmentError("个性化作业题目数量不完整")
        lesson_ids: list[str] = []
        for item in lessons:
            lesson_ids.append(item["lessonId"])
            self.store.save_lesson({
                "lessonId": item["lessonId"],
                "title": item["title"],
                "version": 1,
                "status": "draft",
                "knowledgePoints": [item["metadata"]["planningTopicKey"]],
                "blocks": [],
                "questionPayload": item["questionPayload"],
                "guideCards": item["guideCards"],
            })
        publication_id = uuid.uuid4().hex
        try:
            self.store.create_publication(
                publication_id=publication_id,
                title=f"个性化作业 · {source_plan['publicationId']}",
                source_upload_id=None,
                lesson_ids=lesson_ids,
                status="draft",
                created_at=time.time(),
            )
            self.store.update_publication_status(publication_id, "in_review")
            self.store.update_publication_status(publication_id, "published")
        except Exception as error:  # noqa: BLE001
            raise PersonalizedAssignmentError("个性化试卷未通过发布门禁") from error

        final_plan_id = uuid.uuid4().hex
        final_result = {
            "personalized": True,
            "personalizedVersion": "personalized-assignment-v1",
            "fallback": False,
            "fallbackReason": None,
            "sourcePlanId": source_plan["planId"],
            "sourcePublicationId": source_plan["publicationId"],
            "goals": source_plan.get("result", {}).get("goals", []),
            "coverage": source_plan.get("result", {}).get("coverage", []),
            "mastery": source_plan.get("result", {}).get("mastery", []),
            "errorStats": source_plan.get("result", {}).get("errorStats", []),
            "lessons": [
                {"lessonId": item["lessonId"], **item["metadata"]}
                for item in lessons
            ],
        }
        final_plan = self.planning_service.planning_store.create_personalized_plan(
            plan_id=final_plan_id,
            class_id=class_id,
            publication_id=publication_id,
            source_fingerprint=source_plan["sourceFingerprint"],
            input_snapshot={
                "sourcePlanId": source_plan["planId"],
                "sourcePublicationId": source_plan["publicationId"],
                "questionCount": question_count,
            },
            result=final_result,
            run_id=None,
            created_at=time.time(),
        )
        updated_result = {**(source_plan.get("result") or {}), "personalizedFinalPlanId": final_plan_id}
        self.planning_service.planning_store.update_result(
            source_plan["planId"], updated_result, updated_at=time.time(),
        )
        return final_plan
