"""HTTP boundary for creating tutor threads and appending turns."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from observability import log_event
from domain.contracts.tutoring import TutorMessageRequest


def has_meaningful_answer(content: str, interaction_result: dict[str, Any]) -> bool:
    """Return whether a turn contains an answer a learner could have entered.

    Checking only whether ``interaction_result`` is non-empty is insufficient:
    an untouched choice control serializes as ``{"selectedOptions": []}``.
    Keeping the rule at the HTTP boundary gives every client the same validation
    while the tutor can assume that an ``answer`` turn contains useful input.
    """
    if content.strip():
        return True
    for value in interaction_result.values():
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, list) and value:
            return True
        if isinstance(value, dict) and has_meaningful_answer("", value):
            return True
    return False


def build_tutoring_router(*, mistake_store: Any, tutoring_store: Any, tutor: Any) -> APIRouter:
    """Build the tutoring HTTP adapter from replaceable domain dependencies.

    The demo uses ``local-demo`` as its single learner identity.  This ownership
    check prevents accidental cross-record access during local testing, but it
    is not authentication.  A public deployment must derive the learner from a
    trusted login session instead.
    """
    router = APIRouter(tags=["tutoring"])

    @router.post("/api/mistakes/{mistake_id}/thread")
    def create_thread(mistake_id: str, learnerId: str = "local-demo") -> dict[str, Any]:
        """Create or restore the single tutoring thread for one confirmed mistake."""
        mistake = mistake_store.get(mistake_id)
        if not mistake:
            raise HTTPException(status_code=404, detail="错题不存在")
        if mistake["learnerId"] != learnerId:
            raise HTTPException(status_code=403, detail="不能访问其他学生的错题")
        if mistake["status"] == "pending_confirmation":
            raise HTTPException(status_code=409, detail="请先确认题目和错误原因，再开始陪练")
        if mistake["status"] == "archived":
            raise HTTPException(status_code=409, detail="归档错题不能开始陪练")
        thread = tutoring_store.create_or_get(mistake_id, learnerId)
        log_event("tutor.thread.ready", thread_id=thread["threadId"], mistake_id=mistake_id)
        # create_or_get 可能返回不含消息的轻量记录；统一补载消息，让创建与恢复拥有相同响应结构。
        return tutoring_store.get(thread["threadId"]) or thread

    @router.get("/api/tutor/threads/{thread_id}")
    def get_thread(thread_id: str) -> dict[str, Any]:
        """Return bounded persisted messages and the current tutoring state."""
        thread = tutoring_store.get(thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="辅导线程不存在")
        return thread

    @router.post("/api/tutor/threads/{thread_id}/messages")
    def append_message(thread_id: str, request: TutorMessageRequest) -> dict[str, Any]:
        """Evaluate one learner turn, generate guidance and persist both messages."""
        thread = tutoring_store.get(thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="辅导线程不存在")
        mistake = mistake_store.get(thread["mistakeId"])
        if not mistake:
            raise HTTPException(status_code=404, detail="原错题不存在")
        if request.mode == "answer" and not has_meaningful_answer(
            request.content,
            request.interactionResult,
        ):
            raise HTTPException(status_code=422, detail="请先输入或选择答案")

        result = tutor.reply(
            mistake=mistake,
            thread=thread,
            recent_messages=tutoring_store.recent_messages(thread_id),
            request=request,
        )
        saved = tutoring_store.append_turn(
            thread_id,
            student_content=request.content.strip() or "请求下一步提示",
            input_mode=result["inputMode"],
            assistant_content=result["reply"].reply,
            assessment=result["action"]["assessment"],
            action=result["action"],
            model_run=result["reply"].modelRun,
            stage=result["stage"],
            hint_level=result["reply"].nextHintLevel,
            summary=result["summary"],
        )
        log_event(
            "tutor.turn.completed",
            thread_id=thread_id,
            mistake_id=thread["mistakeId"],
            stage=result["stage"],
            assessment=result["action"]["assessment"],
            source=result["reply"].source,
        )
        return {
            "thread": saved,
            "reply": result["reply"].model_dump(),
            "action": result["action"],
        }

    return router
