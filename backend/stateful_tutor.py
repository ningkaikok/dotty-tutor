"""State machine for one-question, multi-turn mistake tutoring."""

from __future__ import annotations

from typing import Any

from question_contracts import HelpRequest
from tutor_engine import TutorEngine


STAGE_LABELS = {
    "diagnose": "定位卡点",
    "explain": "解释误区",
    "practice": "引导练习",
    "verify": "准备验证",
}


class StatefulTutor:
    """Add a small persisted state machine around the existing ``TutorEngine``.

    The split is intentional: ``TutorEngine`` owns deterministic answer checks
    and one-turn guidance, while this class owns cross-request teaching state.
    The language model can explain and ask questions, but it cannot override an
    answer assessment or move a mistake to ``mastered``.

    Phase-three transitions are deliberately easy to inspect::

        diagnose --wrong/help--> explain --correct--> practice
        diagnose --correct---------------------------> practice
        practice --wrong--> explain
        practice --correct--> verify --any--> verify

    Phase four will add variant questions and mastery verification after
    ``verify`` rather than weakening this boundary.
    """

    def __init__(self, *, runtime: Any) -> None:
        self.runtime = runtime

    def reply(
        self,
        *,
        mistake: dict[str, Any],
        thread: dict[str, Any],
        recent_messages: list[dict[str, Any]],
        request: Any,
    ) -> dict[str, Any]:
        """Produce one reply and the next durable state without writing storage."""
        payload = mistake["questionPayload"]
        question_id = payload["question"]["id"]
        cards = mistake.get("guideCards") or []
        if not cards:
            cards = [{
                "level": 0,
                "stuckAt": "需要先定位这道错题的卡点。",
                "knowledge": [mistake.get("knowledgePoint", "当前知识点")],
                "hint": "先说说你原来是从哪一步开始不确定的。",
                "question": "你当时是怎么想的？",
                "canvasAction": "show-base",
            }]
        engine = TutorEngine(
            lesson_store={question_id: {"payload": payload, "guideCards": cards}},
            runtime=self.runtime,
            guide_cards=cards,
        )
        context = self._conversation_context(thread, recent_messages, mistake)
        tutor_reply = engine.reply(HelpRequest(
            questionId=question_id,
            studentInput=request.content,
            hintLevel=request.hintLevel,
            mode=request.mode,
            interactionResult=request.interactionResult,
            language="zh",
        ), conversation_context=context)
        assessment = str(tutor_reply.guideContext.get("assessment") or "partial")
        next_stage = self._next_stage(thread["stage"], assessment, request.mode)
        action = {
            "type": "advance_stage" if next_stage != thread["stage"] else "continue_stage",
            "previousStage": thread["stage"],
            "nextStage": next_stage,
            "assessment": assessment,
            "prompt": tutor_reply.guideContext.get("question", ""),
        }
        summary = self._updated_summary(
            thread.get("summary", ""),
            current_stage=thread["stage"],
            next_stage=next_stage,
            student=request.content,
            assistant=tutor_reply.reply,
            assessment=assessment,
        )
        return {
            "reply": tutor_reply,
            "stage": next_stage,
            "action": action,
            "summary": summary,
            "inputMode": "structured" if request.interactionResult else "text",
        }

    @staticmethod
    def _next_stage(current: str, assessment: str, mode: str) -> str:
        # 阶段三最多走到 verify；掌握迁移和连续答对属于阶段四，模型本身无权把错题标记为已掌握。
        if assessment == "correct":
            return {
                "diagnose": "practice",
                "explain": "practice",
                "practice": "verify",
                "verify": "verify",
            }.get(current, "explain")
        if current == "diagnose" or assessment == "incorrect":
            return "explain"
        return current if mode == "help" else "explain"

    @staticmethod
    def _conversation_context(
        thread: dict[str, Any],
        recent_messages: list[dict[str, Any]],
        mistake: dict[str, Any],
    ) -> str:
        """Build a bounded prompt view from durable summary and recent turns.

        The database remains the audit source of truth. Only the last part of
        the summary and six messages enter a model call, bounding both latency
        and token cost as a thread grows.
        """
        lines = [
            f"当前阶段：{STAGE_LABELS.get(thread['stage'], thread['stage'])}",
            f"学生确认的错误原因：{mistake.get('errorReason') or '未填写'}",
        ]
        if thread.get("summary"):
            lines.append(f"历史摘要：{thread['summary'][-1200:]}")
        for message in recent_messages[-6:]:
            role = "学生" if message["role"] == "student" else "老师"
            lines.append(f"{role}：{message['content'][:300]}")
        return "\n".join(lines)[-2_400:]

    @staticmethod
    def _updated_summary(
        previous: str,
        *,
        current_stage: str,
        next_stage: str,
        student: str,
        assistant: str,
        assessment: str,
    ) -> str:
        """Append a compact, capped audit summary for the next model call."""
        turn = (
            f"[{current_stage}→{next_stage}/{assessment}] "
            f"学生：{student[:240] or '请求提示'}；老师：{assistant[:320]}"
        )
        return f"{previous}\n{turn}".strip()[-2_000:]
