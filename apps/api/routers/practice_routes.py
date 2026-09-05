"""HTTP boundary for adaptive variation generation and assessment."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from answer_evaluator import evaluate_structured_answer
from domain.constants import DEMO_LEARNER_ID
from domain.contracts.practice import VariationAnswerRequest
from domain.questions.student_view import student_question_payload
from observability import log_event
from routers.tutoring_routes import has_meaningful_answer


def _public_variation(variation: dict[str, Any]) -> dict[str, Any]:
    """变式题记录进入学生端前投影题目本身；其余字段（状态、反馈、掌握度）原样保留。

    判题在服务端完成（``evaluate_structured_answer``），学生端不需要标准答案，
    因此这里剥掉答案不会影响任何渲染或作答流程。
    """
    return {
        **variation,
        "questionPayload": student_question_payload(variation.get("questionPayload")),
    }


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
    def list_variations(
        mistake_id: str, learnerId: str = DEMO_LEARNER_ID
    ) -> dict[str, Any]:
        """列出该错题的变式题。"""
        # 变式题是掌握验证的载体：此前这里原样返回 questionPayload，答案随题目
        # 一起下发，验证等于失效；归属校验也漏了，与紧邻的 /evidence 不一致。
        # docstring 会进入公开 OpenAPI 描述，所以这段说明留在注释里。
        mistake = mistake_store.get(mistake_id)
        if not mistake:
            raise HTTPException(status_code=404, detail="错题不存在")
        if mistake["learnerId"] != learnerId:
            raise HTTPException(status_code=403, detail="不能访问其他学生的错题")
        return {
            "items": [
                _public_variation(variation)
                for variation in variation_store.list_for_mistake(mistake_id)
            ]
        }

    @router.get("/api/mistakes/{mistake_id}/evidence")
    def get_mistake_evidence(
        mistake_id: str, learnerId: str = DEMO_LEARNER_ID
    ) -> dict[str, Any]:
        """Return the explainable evidence chain for one mistake."""
        mistake = mistake_store.get(mistake_id)
        if not mistake:
            raise HTTPException(status_code=404, detail="错题不存在")
        if mistake["learnerId"] != learnerId:
            raise HTTPException(status_code=403, detail="不能访问其他学生的错题")
        variations = []
        for variation in variation_store.list_for_mistake(mistake_id):
            question = (variation.get("questionPayload") or {}).get("question") or {}
            variations.append({
                "variationId": variation["variationId"],
                "sequence": variation["sequence"],
                "strategy": question.get("variationStrategy") or variation["strategy"],
                "strategyVersion": question.get("variationStrategyVersion"),
                "target": question.get("variationTarget") or mistake.get("errorReason"),
                "attributionSource": variation.get("attributionSource") or "unknown",
                "objective": question.get("variationObjective"),
                "level": question.get("variationLevel") or variation["level"],
                "attempts": variation_store.list_attempts(variation["variationId"]),
            })
        reviews = review_store.list_for_mistake(mistake_id)
        return {
            "mistakeId": mistake_id,
            "learnerId": learnerId,
            "errorReason": mistake.get("errorReason"),
            "status": mistake["status"],
            "masteryTransition": "unmastered → mastered" if mistake["status"] == "mastered" else "unmastered",
            "variations": variations,
            "reviewTasks": reviews,
        }

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
            return _public_variation(existing[-1])

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
            attribution_source=generated.get("attributionSource", "unknown"),
        )
        log_event(
            "variation.created",
            mistake_id=mistake_id,
            variation_id=item["variationId"],
            strategy=item["strategy"],
            variation_level=item["level"],
        )
        return _public_variation(item)

    @router.post("/api/variations/{variation_id}/answer")
    def answer_variation(
        variation_id: str,
        request: VariationAnswerRequest,
    ) -> dict[str, Any]:
        item = variation_store.get(variation_id)
        if not item:
            raise HTTPException(status_code=404, detail="变式题不存在")
        existing_attempt = variation_store.get_attempt(request.attemptId) if request.attemptId else None
        if existing_attempt and existing_attempt["variationId"] != variation_id:
            raise HTTPException(status_code=409, detail="attemptId 已用于其他变式题")
        if item["status"] == "answered" and item["assessment"] == "correct":
            if existing_attempt and existing_attempt["variationId"] == variation_id:
                item["attemptId"] = existing_attempt["attemptId"]
                item["evaluationEvidence"] = existing_attempt["evaluationEvidence"]
                item["mastery"] = variation_store.mastery_summary(item["mistakeId"])
                item["reviewTasks"] = review_store.list_for_mistake(item["mistakeId"])
                return _public_variation(item)
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
            attempt_id=request.attemptId,
            response=response,
            assessment=result["assessment"],
            feedback=result["reply"],
            evaluation_evidence=result.get("evaluationEvidence"),
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
        return _public_variation(saved)

    return router
