"""HTTP boundary for scheduled review practice and progress summaries."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy.engine import Engine

from answer_evaluator import evaluate_structured_answer
from application.services.learning_funnel import build_funnel_snapshot
from domain.constants import DEMO_LEARNER_ID
from domain.contracts.practice import VariationAnswerRequest
from domain.learning.mastery_policy import decide_next_action, resolve_policy
from domain.questions.student_view import student_review_task
from observability import log_event
from routers.tutoring_routes import has_meaningful_answer


def review_policy_view(task: dict[str, Any], history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Attach the deterministic policy/gate projection without exposing internals."""
    profile = str(task.get("profile") or "unknown:legacy")
    objective, _, mode = profile.partition(":")
    try:
        policy = resolve_policy(
            task.get("objectiveType") or objective,
            task.get("gateMode") or mode,
            task.get("policyVersion") or task.get("scheduleVersion"),
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=f"复习任务策略无效：{error}") from error
    completed = [item for item in (history or []) if item.get("status") == "completed"]
    if task.get("status") == "completed" and task not in completed:
        completed.append(task)
    correct = sum(item.get("assessment") == "correct" for item in completed)
    evidence_count = len(completed)
    accuracy = correct / evidence_count if evidence_count else 0.0
    raw_evidence = task.get("evaluationEvidence")
    evidence: dict[str, Any] = raw_evidence if isinstance(raw_evidence, dict) else {}
    refs = evidence.get("evidenceRefs") if isinstance(evidence.get("evidenceRefs"), list) else []
    decision = decide_next_action(
        policy,
        accuracy=accuracy,
        evidence_count=evidence_count,
        rubric_passed=evidence.get("rubricPassed") if "rubricPassed" in evidence else None,
        confidence=evidence.get("confidence"),
        evidence_refs=refs,
        assessment=task.get("assessment"),
        sequence_no=int(task.get("sequenceNo") or 0),
    )
    task["policy"] = {
        "objectiveType": policy.objective_type,
        "gateMode": policy.gate_mode,
        "policyVersion": policy.policy_version,
        "profile": policy.profile,
    }
    task["gate"] = decision["gate"]
    task["nextAction"] = decision["nextAction"]
    return task


def build_review_router(
    *,
    mistake_store: Any,
    review_store: Any,
    variation_service: Any,
    engine: Engine | None = None,
) -> APIRouter:
    router = APIRouter(tags=["review"])

    @router.get("/api/funnel")
    def get_learning_funnel(learnerId: str = DEMO_LEARNER_ID) -> dict[str, Any]:
        """学习效果漏斗快照（只读聚合）；engine 未注入时明确返回不可用。"""
        if engine is None:
            raise HTTPException(status_code=503, detail="漏斗聚合需要数据库连接")
        return build_funnel_snapshot(engine, learnerId)

    @router.get("/api/reviews")
    def list_reviews(learnerId: str = DEMO_LEARNER_ID) -> dict[str, Any]:
        items = review_store.list_for_learner(learnerId)
        for item in items:
            mistake = mistake_store.get(item["mistakeId"])
            item["mistake"] = {
                "chapter": mistake["chapter"],
                "knowledgePoint": mistake["knowledgePoint"],
                "prompt": mistake["questionPayload"]["question"]["prompt"],
            } if mistake else None
        return {
            "items": [student_review_task(review_policy_view(
                item,
                review_store.list_for_mistake(item["mistakeId"]),
            )) for item in items],
            "serverTime": time.time(),
        }

    @router.get("/api/progress")
    def get_progress(learnerId: str = DEMO_LEARNER_ID) -> dict[str, Any]:
        mistakes = [item for item in mistake_store.list(learnerId) if item["status"] != "pending_confirmation"]
        tasks = review_store.list_for_learner(learnerId)
        now = time.time()
        completed = [task for task in tasks if task["status"] == "completed"]
        due = [task for task in tasks if task["status"] in {"scheduled", "ready"} and task["dueAt"] <= now]
        mastered = [item for item in mistakes if item["status"] == "mastered"]
        knowledge: dict[str, dict[str, Any]] = defaultdict(lambda: {"total": 0, "mastered": 0})
        for item in mistakes:
            point = item["knowledgePoint"] or "未分类"
            knowledge[point]["total"] += 1
            knowledge[point]["mastered"] += int(item["status"] == "mastered")
        effect: dict[str, Any] = {}
        if engine is not None:
            funnel = build_funnel_snapshot(engine, learnerId)
            effect = funnel.get("learningEffect", {})
            verification = funnel.get("verification", {})
            review = funnel.get("review", {})
        else:
            verification = {}
            review = {}
        return {
            "learnerId": learnerId,
            "totalMistakes": len(mistakes),
            "masteredCount": len(mastered),
            "masteryRate": round(len(mastered) / len(mistakes), 4) if mistakes else 0,
            "dueReviewCount": len(due),
            "completedReviewCount": len(completed),
            "reviewAccuracy": round(
                sum(task["assessment"] == "correct" for task in completed) / len(completed), 4
            ) if completed else 0,
            "verificationAccuracy": verification.get("passRate"),
            "reviewCompletionRate": review.get("completionRate", round(
                len(completed) / len(tasks), 4
            ) if tasks else None),
            "sameKnowledgePointReerrorRate": effect.get("sameKnowledgePointReerrorRate"),
            "knowledgePoints": [
                {"knowledgePoint": point, **counts}
                for point, counts in sorted(knowledge.items())
            ],
        }

    @router.post("/api/reviews/{task_id}/start")
    def start_review(task_id: str) -> dict[str, Any]:
        task = review_store.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="复习任务不存在")
        if task["status"] == "ready":
            return student_review_task(review_policy_view(task, review_store.list_for_mistake(task["mistakeId"])))
        if task["status"] != "scheduled":
            raise HTTPException(status_code=409, detail="这项复习任务已经完成")
        mistake = mistake_store.get(task["mistakeId"])
        if not mistake or mistake["learnerId"] != task["learnerId"]:
            raise HTTPException(status_code=404, detail="复习任务对应的错题不存在")
        try:
            generated = variation_service.generate(mistake, 3)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        started = review_store.start(
            task_id,
            question_payload=generated["questionPayload"],
            model_run=generated["modelRun"],
        )
        if not started:
            raise HTTPException(status_code=409, detail="复习任务状态已变化，请刷新")
        log_event("review.started", task_id=task_id, mistake_id=task["mistakeId"])
        return student_review_task(review_policy_view(started, review_store.list_for_mistake(task["mistakeId"])))

    @router.post("/api/reviews/{task_id}/answer")
    def answer_review(task_id: str, request: VariationAnswerRequest) -> dict[str, Any]:
        task = review_store.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="复习任务不存在")
        if task["status"] == "completed":
            replay_response = {"content": request.content, "interactionResult": request.interactionResult}
            if replay_response != (task.get("response") or {}):
                raise HTTPException(status_code=409, detail="这项复习任务已提交其他答案")
            history = review_store.list_for_mistake(task["mistakeId"])
            enriched = review_policy_view(task, history)
            evidence = task.get("evaluationEvidence") or {}
            try:
                saved = review_store.answer_and_schedule_follow_up(
                    task_id,
                    response=task.get("response") or {},
                    assessment=task.get("assessment") or "",
                    feedback=task.get("feedback") or "",
                    evaluation_evidence=evidence,
                    next_action=enriched["nextAction"],
                    trigger_evidence_ref=evidence.get("evidenceRef"),
                )
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            if not saved:
                raise HTTPException(status_code=409, detail="这项复习任务状态已变化，请刷新")
            history = review_store.list_for_mistake(task["mistakeId"])
            return student_review_task(review_policy_view(saved, history))
        if task["status"] != "ready" or not task["questionPayload"]:
            raise HTTPException(status_code=409, detail="请先开始尚未完成的复习任务")
        if not has_meaningful_answer(request.content, request.interactionResult):
            raise HTTPException(status_code=422, detail="请先输入或选择答案")
        result = evaluate_structured_answer(
            task["questionPayload"]["question"],
            request.content,
            request.interactionResult,
        )
        if not result:
            raise HTTPException(status_code=422, detail="复习题缺少可确定判定的答案结构")
        response = {"content": request.content, "interactionResult": request.interactionResult}
        evidence = result.get("evaluationEvidence") or {}
        history = review_store.list_for_mistake(task["mistakeId"])
        pending = dict(task)
        pending.update(
            status="completed",
            response=response,
            evaluationEvidence=evidence,
            assessment=result["assessment"],
            feedback=result["reply"],
        )
        pending_view = review_policy_view(pending, history + [pending])
        try:
            saved = review_store.answer_and_schedule_follow_up(
                task_id,
                response=response,
                assessment=result["assessment"],
                feedback=result["reply"],
                evaluation_evidence=evidence,
                next_action=pending_view["nextAction"],
                trigger_evidence_ref=evidence.get("evidenceRef"),
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if not saved:
            raise HTTPException(status_code=409, detail="这项复习任务状态已变化，请刷新")
        history = review_store.list_for_mistake(task["mistakeId"])
        enriched = review_policy_view(saved, history)
        log_event(
            "review.completed",
            task_id=task_id,
            mistake_id=task["mistakeId"],
            assessment=result["assessment"],
        )
        return student_review_task(enriched)

    return router
