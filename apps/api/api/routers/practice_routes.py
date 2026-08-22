"""HTTP boundary for adaptive variation generation and assessment."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from answer_evaluator import evaluate_structured_answer
from api.routers.tutoring_routes import has_meaningful_answer
from domain.constants import DEMO_LEARNER_ID
from domain.contracts.practice import VariationAnswerRequest
from observability import log_event


def build_practice_router(
    *,
    mistake_store: Any,
    tutoring_store: Any,
    variation_store: Any,
    variation_service: Any,
    review_store: Any,
) -> APIRouter:
    router = APIRouter(tags=["practice"])

    @router.get("/api/mistakes/{mistake_id}/variations")
    def list_variations(mistake_id: str) -> dict[str, Any]:
        if not mistake_store.get(mistake_id):
            raise HTTPException(status_code=404, detail="错题不存在")
        return {"items": variation_store.list_for_mistake(mistake_id)}

    @router.post("/api/mistakes/{mistake_id}/variations")
    def create_variation(
        mistake_id: str, learnerId: str = DEMO_LEARNER_ID
    ) -> dict[str, Any]:
        mistake = mistake_store.get(mistake_id)
        if not mistake:
            raise HTTPException(status_code=404, detail="错题不存在")
        if mistake["learnerId"] != learnerId:
            raise HTTPException(status_code=403, detail="不能访问其他学生的错题")
        if mistake["status"] != "unmastered":
            raise HTTPException(status_code=409, detail="只有待掌握错题可以生成验证题")
        thread = tutoring_store.find_for_mistake(mistake_id, learnerId)
        if not thread or thread["stage"] not in {"practice", "verify"}:
            raise HTTPException(status_code=409, detail="请先完成原题纠错，再开始变式练习")

        # 验证阶段只需要一道题。前端刷新、重复点击或阶段从 practice
        # 推进到 verify 时都复用这条记录，避免再次触发昂贵的模型生成。
        existing = variation_store.list_for_mistake(mistake_id)
        if existing:
            log_event(
                "variation.reused",
                mistake_id=mistake_id,
                variation_id=existing[-1]["variationId"],
                reason="single-validation-question",
            )
            return existing[-1]

        sequence = 1
        try:
            generated = variation_service.generate(mistake, sequence)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        item = variation_store.create(
            mistake_id=mistake_id,
            learner_id=learnerId,
            strategy=generated["strategy"],
            level=generated["level"],
            question_payload=generated["questionPayload"],
            model_run=generated["modelRun"],
        )
        log_event(
            "variation.created",
            mistake_id=mistake_id,
            variation_id=item["variationId"],
            strategy=item["strategy"],
            variation_level=item["level"],
        )
        return item

    @router.post("/api/variations/{variation_id}/answer")
    def answer_variation(
        variation_id: str,
        request: VariationAnswerRequest,
    ) -> dict[str, Any]:
        item = variation_store.get(variation_id)
        if not item:
            raise HTTPException(status_code=404, detail="变式题不存在")
        if item["status"] == "answered" and item["assessment"] == "correct":
            raise HTTPException(status_code=409, detail="这道验证题已经答对")
        if not has_meaningful_answer(request.content, request.interactionResult):
            raise HTTPException(status_code=422, detail="请先输入或选择答案")
        result = evaluate_structured_answer(
            item["questionPayload"]["question"],
            request.content,
            request.interactionResult,
        )
        if not result:
            raise HTTPException(
                status_code=422,
                detail="这道变式题缺少可确定判定的答案结构，请重新生成",
            )
        response = {
            "content": request.content,
            "interactionResult": request.interactionResult,
        }
        saved = variation_store.answer(
            variation_id,
            response=response,
            assessment=result["assessment"],
            feedback=result["reply"],
        )
        if not saved:
            raise HTTPException(status_code=409, detail="这道验证题已经答对")
        mastery = variation_store.mastery_summary(item["mistakeId"])
        thread_stage = None
        thread = tutoring_store.find_for_mistake(item["mistakeId"], item["learnerId"])
        # 变式答对是一个确定性教学事件：practice 完成，进入 verify。
        # 掌握度只需要这一道验证题答对，不再额外生成第二道题。
        if result["assessment"] == "correct" and thread and thread["stage"] == "practice":
            thread = tutoring_store.advance_stage(
                thread["threadId"],
                "verify",
                summary="首道变式题答对，进入掌握验证",
            )
        if thread:
            thread_stage = thread["stage"]
        if mastery["mastered"]:
            promoted = mistake_store.mark_mastered(item["mistakeId"])
            if not promoted:
                raise HTTPException(status_code=409, detail="错题状态已变化，请刷新后重试")
            log_event(
                "mistake.mastered",
                mistake_id=item["mistakeId"],
                answered_count=mastery["answeredCount"],
            )
            saved["reviewTasks"] = review_store.schedule(
                mistake_id=promoted["mistakeId"],
                learner_id=promoted["learnerId"],
                base_time=saved["answeredAt"],
            )
        saved["mastery"] = mastery
        saved["tutorStage"] = thread_stage
        log_event(
            "variation.answered",
            mistake_id=item["mistakeId"],
            variation_id=variation_id,
            assessment=result["assessment"],
        )
        return saved

    return router
