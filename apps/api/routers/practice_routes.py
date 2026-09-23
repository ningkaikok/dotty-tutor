"""HTTP boundary for adaptive variation generation and assessment."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from answer_evaluator import evaluate_structured_answer
from domain.constants import DEMO_LEARNER_ID
from domain.contracts.practice import VariationAnswerRequest
from domain.learning.mastery_policy import decide_next_action, resolve_policy
from domain.questions.student_view import student_review_task, student_variation_item
from observability import log_event
from routers.review_routes import review_policy_view
from routers.tutoring_routes import has_meaningful_answer


def _public_variation(variation: dict[str, Any]) -> dict[str, Any]:
    """变式题记录进入学生端前投影题目本身；其余字段（状态、反馈、掌握度）原样保留。

    判题在服务端完成（``evaluate_structured_answer``），学生端不需要标准答案，
    因此这里剥掉答案不会影响任何渲染或作答流程。
    """
    return student_variation_item(variation)


def _variation_policy(item: dict[str, Any]):
    question = (item.get("questionPayload") or {}).get("question") or {}
    try:
        return resolve_policy(
            question.get("objectiveType") or question.get("objective_type"),
            question.get("gateMode") or question.get("gate_mode"),
            question.get("policyVersion") or question.get("policy_version"),
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=f"变式题策略无效：{error}") from error


def _mastery_view(variation_store: Any, item: dict[str, Any]) -> dict[str, Any]:
    """Project immutable variation evidence through the versioned domain gate."""
    policy = _variation_policy(item)
    variations = variation_store.list_for_mistake(item["mistakeId"])
    attempts = [
        attempt
        for variation in variations
        for attempt in variation_store.list_attempts(variation["variationId"])
    ]
    eligible = [
        attempt for attempt in attempts
        if attempt.get("assessment") in {"correct", "incorrect"}
        and (attempt.get("evaluationEvidence") or {}).get("masteryEligible", True) is not False
    ]
    correct = sum(attempt.get("assessment") == "correct" for attempt in eligible)
    evidence_count = len(eligible)
    accuracy = correct / evidence_count if evidence_count else 0.0
    latest = eligible[-1] if eligible else {}
    evidence = latest.get("evaluationEvidence") or {}
    refs = evidence.get("evidenceRefs") if isinstance(evidence.get("evidenceRefs"), list) else []
    decision = decide_next_action(
        policy,
        accuracy=accuracy,
        evidence_count=evidence_count,
        rubric_passed=evidence.get("rubricPassed"),
        confidence=evidence.get("confidence"),
        evidence_refs=refs,
        assessment=latest.get("assessment"),
        sequence_no=int(item.get("sequence") or 0),
    )
    legacy = variation_store.mastery_summary(item["mistakeId"])
    return {
        **legacy,
        "requiredCorrect": 1 if policy.gate_mode == "legacy" else policy.quantitative_min_evidence,
        "answeredCount": evidence_count,
        "accuracy": accuracy,
        "evidenceCount": evidence_count,
        "mastered": decision["nextAction"] in {"advance", "test_out"},
        "policy": {
            "objectiveType": policy.objective_type,
            "gateMode": policy.gate_mode,
            "policyVersion": policy.policy_version,
            "profile": policy.profile,
        },
        "gate": decision["gate"],
        "nextAction": decision["nextAction"],
    }


def _with_mastery(item: dict[str, Any], mastery: dict[str, Any]) -> dict[str, Any]:
    item["mastery"] = mastery
    item["policy"] = mastery["policy"]
    item["gate"] = mastery["gate"]
    item["nextAction"] = mastery["nextAction"]
    return item


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
                _public_variation(_with_mastery(
                    variation,
                    _mastery_view(variation_store, variation),
                ))
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
            "reviewTasks": [student_review_task(review_policy_view(review, reviews)) for review in reviews],
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

        # 已答错的题仍允许在原题上修正；答对但领域 gate 未通过时生成下一道
        # sequence，保留此前题目和 attempt 历史，不把第一道题永久复用。
        existing = variation_store.list_for_mistake(mistake_id)
        if existing:
            latest = existing[-1]
            if latest["status"] == "ready" or latest["assessment"] == "incorrect":
                log_event(
                    "variation.reused",
                    mistake_id=mistake_id,
                    variation_id=latest["variationId"],
                    reason="awaiting-answer-or-correction",
                )
                return _public_variation(_with_mastery(
                    latest,
                    _mastery_view(variation_store, latest),
                ))
            mastery = _mastery_view(variation_store, latest)
            if mastery["mastered"]:
                raise HTTPException(status_code=409, detail="这道错题已经完成掌握验证")
            sequence = int(latest.get("sequence") or len(existing)) + 1
        else:
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
        return _public_variation(_with_mastery(item, _mastery_view(variation_store, item)))

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
                _with_mastery(item, _mastery_view(variation_store, item))
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
        mastery = _mastery_view(variation_store, saved)
        thread_stage = None
        thread = tutoring_store.find_for_mistake(item["mistakeId"], item["learnerId"])
        # 只有领域 gate 通过才推进 Tutor 阶段；模型/判题器的 correct 不能绕过 gate。
        if (
            result["assessment"] == "correct"
            and mastery["nextAction"] in {"advance", "test_out"}
            and thread
            and thread["stage"] == "practice"
        ):
            thread = tutoring_store.advance_stage(
                thread["threadId"],
                "verify",
                summary="变式题通过领域门槛，进入掌握验证",
            )
        if thread:
            thread_stage = thread["stage"]
        if mastery["nextAction"] in {"advance", "test_out"}:
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
                objective_type=(item.get("questionPayload", {}).get("question", {}).get("objectiveType")
                                if isinstance(item.get("questionPayload"), dict) else None),
                gate_mode=(item.get("questionPayload", {}).get("question", {}).get("gateMode")
                           if isinstance(item.get("questionPayload"), dict) else None),
                policy_version=(item.get("questionPayload", {}).get("question", {}).get("policyVersion")
                                if isinstance(item.get("questionPayload"), dict) else None),
                trigger_evidence_ref=(result.get("evaluationEvidence") or {}).get("evidenceRef"),
            )
        _with_mastery(saved, mastery)
        saved["tutorStage"] = thread_stage
        saved["policy"] = mastery["policy"]
        saved["gate"] = mastery["gate"]
        saved["nextAction"] = mastery["nextAction"]
        log_event(
            "variation.answered",
            mistake_id=item["mistakeId"],
            variation_id=variation_id,
            assessment=result["assessment"],
        )
        return _public_variation(saved)

    return router
