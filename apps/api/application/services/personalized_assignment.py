"""Generate one privacy-bounded, class-level personalized assignment."""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

from application.services.lesson_generation import (
    PersonalizedLessonGenerationError,
    generate_personalized_lessons,
)
from observability import log_event


class PersonalizedAssignmentError(ValueError):
    """A personalized assignment was rejected before it became assignable."""


def personalized_plan_id(source_plan_id: str) -> str:
    """一个来源计划只对应一个最终计划，因此 ID 由来源推导而不是随机生成。

    这让 ``assignment_plans`` 的主键本身成为原子声明：并发的第二次生成会在插入
    时撞主键，而不是各自建出一份重复的已发布试卷。沿用 ``knowledge_point_id``
    的 ``sha256(...)[:32]`` 约定。
    """
    digest = hashlib.sha256(f"personalized-final\0{source_plan_id}".encode()).hexdigest()[:32]
    return f"pap-{digest}"


class PersonalizedAssignmentService:
    """Orchestrate planning evidence, one model call, and publication gates."""

    def __init__(self, *, store: Any, planning_service: Any, model_runtime: Any) -> None:
        self.store = store
        self.planning_service = planning_service
        self.model_runtime = model_runtime

    def _publish(self, publication_id: str) -> None:
        """把试卷推进到 published；已发布则跳过，重试因此是安全的。

        状态机允许 ``published → published``，但不允许 ``published → in_review``，
        所以必须先看当前状态，不能无条件走完整条链路。
        """
        publication = self.store.load_publication(publication_id)
        if publication and publication.get("status") == "published":
            return
        try:
            if not publication or publication.get("status") == "draft":
                self.store.update_publication_status(publication_id, "in_review")
            self.store.update_publication_status(publication_id, "published")
        except Exception as error:  # noqa: BLE001
            raise PersonalizedAssignmentError("个性化试卷未通过发布门禁") from error

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
        except Exception as error:  # noqa: BLE001
            raise PersonalizedAssignmentError("个性化试卷未通过发布门禁") from error

        final_plan_id = personalized_plan_id(source_plan["planId"])
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
        # 插入最终计划就是这次生成的原子声明：主键由来源计划推导，并发的第二次
        # 生成会在这里撞主键。声明成功之后才发布试卷——输给竞争的那一份停在
        # draft，而 confirm_and_create_assignment 只接受 published，因此它永远
        # 不会被误派，也不会污染已发布列表。
        try:
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
        except Exception:  # noqa: BLE001
            existing = self.planning_service.get_plan(class_id=class_id, plan_id=final_plan_id)
            if not existing:
                raise
            log_event(
                "personalized_assignment.duplicate_generation_discarded",
                level=30,
                class_id=class_id,
                source_plan_id=source_plan["planId"],
                kept_plan_id=final_plan_id,
                discarded_publication_id=publication_id,
            )
            # 上一次可能在声明之后、发布之前失败，让计划指向一份仍是 draft 的试卷。
            # 重试在这里补完发布，避免卡在“计划已存在但无法指派”的状态。
            self._publish(existing["publicationId"])
            return existing

        self._publish(publication_id)
        updated_result = {**(source_plan.get("result") or {}), "personalizedFinalPlanId": final_plan_id}
        self.planning_service.planning_store.update_result(
            source_plan["planId"], updated_result, updated_at=time.time(),
        )
        return final_plan
