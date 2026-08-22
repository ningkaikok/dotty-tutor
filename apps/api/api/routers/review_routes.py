"""HTTP boundary for scheduled review practice and progress summaries."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy.engine import Engine

from answer_evaluator import evaluate_structured_answer
from api.routers.tutoring_routes import has_meaningful_answer
from application.services.learning_funnel import build_funnel_snapshot
from domain.constants import DEMO_LEARNER_ID
from domain.contracts.practice import VariationAnswerRequest
from observability import log_event


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
        return {"items": items, "serverTime": time.time()}

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
            return task
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
        return started

    @router.post("/api/reviews/{task_id}/answer")
    def answer_review(task_id: str, request: VariationAnswerRequest) -> dict[str, Any]:
        task = review_store.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="复习任务不存在")
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
        saved = review_store.answer(
            task_id,
            response={"content": request.content, "interactionResult": request.interactionResult},
            assessment=result["assessment"],
            feedback=result["reply"],
        )
        if not saved:
            raise HTTPException(status_code=409, detail="这项复习任务已经提交过")
        log_event(
            "review.completed",
            task_id=task_id,
            mistake_id=task["mistakeId"],
            assessment=result["assessment"],
        )
        return saved

    return router
