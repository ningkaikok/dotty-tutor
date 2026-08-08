"""HTTP boundary for adaptive variation generation and assessment."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from answer_evaluator import evaluate_structured_answer
from observability import log_event
from practice_contracts import VariationAnswerRequest
from tutoring_routes import has_meaningful_answer


def build_practice_router(
    *,
    mistake_store: Any,
    tutoring_store: Any,
    variation_store: Any,
    variation_service: Any,
) -> APIRouter:
    router = APIRouter(tags=["practice"])

    @router.get("/api/mistakes/{mistake_id}/variations")
    def list_variations(mistake_id: str) -> dict[str, Any]:
        if not mistake_store.get(mistake_id):
            raise HTTPException(status_code=404, detail="错题不存在")
        return {"items": variation_store.list_for_mistake(mistake_id)}

    @router.post("/api/mistakes/{mistake_id}/variations")
    def create_variation(mistake_id: str, learnerId: str = "local-demo") -> dict[str, Any]:
        mistake = mistake_store.get(mistake_id)
        if not mistake:
            raise HTTPException(status_code=404, detail="错题不存在")
        if mistake["learnerId"] != learnerId:
            raise HTTPException(status_code=403, detail="不能访问其他学生的错题")
        if mistake["status"] != "unmastered":
            raise HTTPException(status_code=409, detail="只有待掌握错题可以生成验证题")
        thread = tutoring_store.find_for_mistake(mistake_id, learnerId)
        if not thread or thread["stage"] != "verify":
            raise HTTPException(status_code=409, detail="请先完成单题陪练，再开始变式验证")

        sequence = variation_store.count_for_mistake(mistake_id) + 1
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
        if item["status"] == "answered":
            raise HTTPException(status_code=409, detail="这道变式题已经提交过")
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
            raise HTTPException(status_code=409, detail="这道变式题已经提交过")
        mastery = variation_store.mastery_summary(item["mistakeId"])
        if mastery["mastered"]:
            promoted = mistake_store.mark_mastered(item["mistakeId"])
            if not promoted:
                raise HTTPException(status_code=409, detail="错题状态已变化，请刷新后重试")
            log_event(
                "mistake.mastered",
                mistake_id=item["mistakeId"],
                answered_count=mastery["answeredCount"],
            )
        saved["mastery"] = mastery
        log_event(
            "variation.answered",
            mistake_id=item["mistakeId"],
            variation_id=variation_id,
            assessment=result["assessment"],
        )
        return saved

    return router
