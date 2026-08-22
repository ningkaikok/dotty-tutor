"""State machine for one-question, multi-turn mistake tutoring."""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from application.services.tutor_engine import TutorEngine
from domain.questions.contracts import HelpRequest, TutorReply
from domain.tutoring.checks import mock_model_run
from domain.tutoring.turn_plan import (
    _has_structured_answer,
    build_tutor_turn_plan,
    infer_student_intent,
    normalize_misconception,
    select_teaching_action,
    teaching_strategy_context,
)

STAGE_LABELS = {
    "diagnose": "定位卡点",
    "explain": "解释误区",
    "practice": "引导练习",
    "verify": "准备验证",
}


class StatefulTutor:
    """围绕既有 ``TutorEngine`` 的最小持久化状态机（单题多轮陪练）。

    这个拆分是有意为之的权限边界：``TutorEngine`` 只负责确定性判题和单轮引导，
    本类独占跨请求的教学状态。语言模型可以解释、提问、给提示，但它**不能**：
    覆盖判题结果（模型说对不算对）、直接把错题标记为 ``mastered``、跳过阶段。

    阶段转移刻意保持一眼可审查::

        diagnose --答错/求助--> explain --答对--> practice
        diagnose --答对-------------------------------> practice
        确认就绪 ------------------------------------> practice
        practice --答对--> verify --任意提交--> verify

    线程进入 ``practice``/``verify`` 后由路由层生成变式题；连续两道确定性判定的
    变式题都答对，错题才会进入 ``mastered``。掌握与否由答案结构决定，不由模型决定。
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
        student_intent = infer_student_intent(
            mode=request.mode,
            content=request.content,
            interaction_result=request.interactionResult,
        )
        # 生成前只使用服务端已经知道的事实选择动作。模型随后可以提出误区
        # 假设，但不能反向改写这次输入的意图或判题权限。
        generation_action = select_teaching_action(
            intent=student_intent["id"],
            error_reason=mistake.get("errorReason"),
            current_stage=thread["stage"],
            assessment="partial",
        )
        # 教学策略必须在生成前进入上下文；只在生成后记录计划会“看起来可审计”，
        # 却不会真正改变老师的下一步行为。
        strategy_context = teaching_strategy_context(
            mistake.get("errorReason"),
            thread["stage"],
            intent=student_intent,
            teaching_action=generation_action,
        )
        model_context = f"{context}\n{strategy_context}"[-2_700:]
        tutor_request = HelpRequest(
            questionId=question_id,
            studentInput=request.content,
            hintLevel=request.hintLevel,
            mode=request.mode,
            interactionResult=request.interactionResult,
            language="zh",
        )
        if student_intent["id"] == "confirm-ready":
            # 这是一个确定性的流程操作，不应再交给模型猜测。此前模型把
            # “准备好了”当成没有新答案，重复追问是否卡住，导致阶段永远停在
            # explain。先显式推进到 practice，首道变式题答对后再进入 verify。
            tutor_reply = TutorReply(
                reply=(
                    "好，我们进入变式练习。接下来生成一道同知识点但不重复原题的练习；"
                    "这道题答错可以修改后重新提交，答对一次即可完成掌握验证。"
                ),
                guideContext={
                    "assessment": "partial",
                    "assessmentAuthority": "deterministic",
                    "stuckAt": "学生已确认可以进入变式练习。",
                    "knowledge": [mistake.get("knowledgePoint", "当前知识点")],
                    "hint": "先完成第一道变式题。",
                    "question": "准备好后开始作答。",
                    "misconception": normalize_misconception(None),
                },
                nextHintLevel=request.hintLevel,
                canvasAction="show-base",
                source="stored-guide-card",
                modelRun=mock_model_run("deterministic"),
            )
            deduplication = {
                "status": "deterministic-ready-transition",
                "retryCount": 0,
                "fallbackUsed": False,
                "similarity": 0.0,
            }
        else:
            tutor_reply = engine.reply(tutor_request, conversation_context=model_context)
            tutor_reply, deduplication = self._deduplicate_reply(
                engine=engine,
                request=tutor_request,
                conversation_context=model_context,
                recent_messages=recent_messages,
                reply=tutor_reply,
            )
        assessment = str(tutor_reply.guideContext.get("assessment") or "partial")
        misconception = normalize_misconception(
            tutor_reply.guideContext.get("misconception"),
            student_input=request.content,
        )
        plan = build_tutor_turn_plan(
            error_reason=mistake.get("errorReason"),
            current_stage=thread["stage"],
            mode=request.mode,
            assessment=assessment,
            reply_source=tutor_reply.source,
            assessment_authority=str(
                tutor_reply.guideContext.get("assessmentAuthority") or "guided"
            ),
            student_intent=student_intent,
            misconception=misconception,
            generation_teaching_action=generation_action,
            evaluation_evidence=tutor_reply.guideContext.get("evaluationEvidence"),
        )
        next_stage = plan["suggestedStage"]
        action = {
            "type": "advance_stage" if next_stage != thread["stage"] else "continue_stage",
            "previousStage": thread["stage"],
            "nextStage": next_stage,
            "assessment": assessment,
            "prompt": tutor_reply.guideContext.get("question", ""),
            "tutorTurnPlan": plan,
            "deduplication": deduplication,
            "modelRun": self._model_audit(tutor_reply.modelRun),
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
            # Empty controls such as ``{"selectedOptions": []}`` are kept
            # accepted at the HTTP boundary but must not be audited as an
            # actual structured answer.
            "inputMode": "structured" if _has_structured_answer(request.interactionResult) else "text",
        }

    @staticmethod
    def _deduplicate_reply(
        *, engine: TutorEngine, request: HelpRequest, conversation_context: str,
        recent_messages: list[dict[str, Any]], reply: Any,
    ) -> tuple[Any, dict[str, Any]]:
        """Avoid repeating a model prompt forever, with one bounded retry.

        确定性判题的措辞允许重复，因为它是可审计结果；模型回复和预制引导卡
        都属于教学提示，可升级 hint level 重试一次。重试仍重复时改用固定的
        下一步问题，避免调用链循环。
        """
        previous = next((item.get("content", "") for item in reversed(recent_messages)
                         if item.get("role") == "assistant"), "")
        similarity = StatefulTutor._reply_similarity(previous, reply.reply)
        if not previous or similarity < 0.76:
            return reply, {
                "status": "not-repeated", "retryCount": 0,
                "fallbackUsed": False, "similarity": round(similarity, 3),
            }
        if reply.guideContext.get("assessmentAuthority") == "deterministic":
            return reply, {"status": "deterministic-repeat-allowed", "retryCount": 0, "fallbackUsed": False}
        retry_request = request.model_copy(update={"hintLevel": min(request.hintLevel + 1, 3)})
        retried = engine.reply(
            retry_request,
            conversation_context=f"{conversation_context}\n本轮必须换一种提示策略，不能复述上一句。",
        )
        retry_similarity = StatefulTutor._reply_similarity(previous, retried.reply)
        if retry_similarity < 0.76:
            return retried, {
                "status": "retry-succeeded", "retryCount": 1,
                "fallbackUsed": False, "similarity": round(retry_similarity, 3),
            }
        fallback = retried.model_copy(update={
            "reply": "我们先不重复刚才的提示。请只写出题目中最关键的一个已知条件，并说明它能用于哪一步。",
            "source": "stored-guide-card",
        })
        return fallback, {
            "status": "fallback-after-retry", "retryCount": 1,
            "fallbackUsed": True, "similarity": round(retry_similarity, 3),
        }

    @staticmethod
    def _reply_similarity(previous: str, candidate: str) -> float:
        """Estimate lexical repetition without adding an embedding dependency.

        连续提示通常只改一两个语气词，严格相等无法识别。字符序列比对对中文
        和公式都可用；阈值只触发一次有界重试，因此比引入第二个模型更稳定。
        """
        def normalized(value: str) -> str:
            return "".join(char for char in value.lower() if char.isalnum() or "\u4e00" <= char <= "\u9fff")

        left, right = normalized(previous), normalized(candidate)
        if not left or not right:
            return 0.0
        if left == right:
            return 1.0
        return SequenceMatcher(None, left, right, autojunk=False).ratio()

    @staticmethod
    def _model_audit(model_run: dict[str, Any]) -> dict[str, Any]:
        """Keep provider metadata beside the action without changing old reply fields."""
        return {key: model_run.get(key) for key in ("requestedProvider", "provider", "model", "fallback") if key in model_run}

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
