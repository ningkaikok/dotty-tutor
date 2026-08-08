"""Tutor response orchestration separated from the HTTP application."""

from __future__ import annotations

import json
from typing import Any

from answer_evaluator import evaluate_structured_answer
from question_contracts import CANVAS_ACTIONS, HELP_SCHEMA, HelpRequest, TutorReply
from tutor_checks import build_reply, equation_conflict, mock_model_run


def _safe_text(value: Any, fallback: str, limit: int = 600) -> str:
    text = str(value or "").strip()
    return (text or fallback)[:limit]


def _safe_string_list(value: Any, fallback: list[str], limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return fallback
    items = [_safe_text(item, "", 160) for item in value]
    return [item for item in items if item][:limit] or fallback


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


class TutorEngine:
    def __init__(self, *, lesson_store: dict[str, dict[str, Any]], runtime: Any, guide_cards: list[dict[str, Any]]) -> None:
        self.lesson_store = lesson_store
        self.runtime = runtime
        self.guide_cards = guide_cards

    def reply(self, request: HelpRequest, *, conversation_context: str = "") -> TutorReply:
        """Return one tutoring reply.

        ``conversation_context`` is an optional bounded summary supplied by the
        stateful mistake tutor. Deterministic answer checks intentionally ignore
        it so previous turns cannot influence an objective result.
        """
        stored = self.lesson_store.get(request.questionId)
        deterministic = self._deterministic_reply(stored, request)
        if deterministic:
            return deterministic
        if not stored or self.runtime.selection.provider == "mock":
            cards = stored["guideCards"] if stored else self.guide_cards
            return build_reply(request, cards)
        return self._model_reply(stored, request, conversation_context)

    def _deterministic_reply(self, stored: dict[str, Any] | None, request: HelpRequest) -> TutorReply | None:
        if not stored or request.mode != "answer":
            return None
        question = stored["payload"].get("question", {})
        structured = evaluate_structured_answer(question, request.studentInput, request.interactionResult)
        if structured:
            return TutorReply(
                reply=structured["reply"],
                guideContext={key: structured[key] for key in ("assessment", "stuckAt", "knowledge", "hint", "question")},
                nextHintLevel=min(request.hintLevel + 1, 3),
                canvasAction="show-base",
                source="answer-check",
                modelRun=mock_model_run(),
            )
        if question.get("questionType") == "true-false" and question.get("correctAnswer"):
            expected = str(question["correctAnswer"]).strip().lower()
            submitted = request.studentInput.strip().lower()
            selected = "正确" if "正确" in submitted or "true" in submitted else "错误" if "错误" in submitted or "false" in submitted else ""
            if selected:
                is_correct = selected.lower() == expected
                return TutorReply(
                    reply=(
                        f"回答正确，答案是“{question['correctAnswer']}”。请再说说题干中的哪个条件支持这个判断。"
                        if is_correct else
                        f"这次选择不对，正确答案是“{question['correctAnswer']}”。请回到题干，找出能验证这句话的条件。"
                    ),
                    guideContext={
                        "assessment": "correct" if is_correct else "incorrect",
                        "stuckAt": "需要根据题干条件判断命题真伪。",
                        "knowledge": [question.get("knowledgePoint", "概念判断")],
                        "hint": "圈出题干中的关键条件，再逐项核对命题。",
                        "question": "题干中的哪条条件能支持你的判断？",
                    },
                    nextHintLevel=min(request.hintLevel + 1, 3),
                    canvasAction="show-base",
                    source="answer-check",
                    modelRun=mock_model_run(),
                )
        if question.get("questionType") == "draw-line":
            interaction = question.get("interaction") or {}
            required = {
                tuple(sorted(pair))
                for pair in interaction.get("requiredConnections", [])
                if isinstance(pair, list) and len(pair) == 2
            }
            submitted = {
                tuple(sorted(pair))
                for pair in request.interactionResult.get("connections", [])
                if isinstance(pair, list) and len(pair) == 2
            }
            if required:
                is_correct = required.issubset(submitted)
                assessment = "correct" if is_correct else "partial" if submitted else "incorrect"
                return TutorReply(
                    reply=(
                        "连接正确。请说明这条线段为什么满足题目要求。"
                        if is_correct else
                        "还差一点：检查是否连接了题目要求的两个端点，再试一次。"
                    ),
                    guideContext={
                        "assessment": assessment,
                        "stuckAt": "需要把题目中的几何关系落实为图上的连线。",
                        "knowledge": [question.get("knowledgePoint", "几何作图")],
                        "hint": interaction.get("instruction", "先找出题目要求连接的两个点。"),
                        "question": "你连接的线段对应题目中的哪条几何关系？",
                    },
                    nextHintLevel=min(request.hintLevel + 1, 3),
                    canvasAction="show-triangles",
                    source="answer-check",
                    modelRun=mock_model_run(),
                )
        return None

    def _model_reply(
        self,
        stored: dict[str, Any],
        request: HelpRequest,
        conversation_context: str = "",
    ) -> TutorReply:
        payload = stored["payload"]
        cards = stored["guideCards"]
        current_card = cards[min(request.hintLevel, len(cards) - 1)]
        conflict = equation_conflict(request.studentInput, payload["lessonSteps"], payload["question"]["prompt"])
        conflict_instruction = ""
        if conflict:
            conflict_instruction = (
                f"系统校验发现学生写的 {conflict[0]} 与标准步骤 {conflict[1]} 冲突。"
                "assessment 必须为 incorrect，绝对不能说这一步正确；只提示学生回查符号和算术。"
            )
        prompt = f"""
你正在辅导下面这道题。先用标准讲解脚本独立核对学生的每一步计算，再判断卡点。

题目：{payload['question']['prompt']}
已知条件：{'；'.join(payload['question']['givens'])}
标准讲解脚本：{_json_dumps(payload['lessonSteps'])}
当前提示层级：{request.hintLevel}
候选引导卡：{_json_dumps(current_card)}
学生输入：{request.studentInput.strip() or '学生没有输入内容'}
学生交互作答结果：{_json_dumps(request.interactionResult) if request.interactionResult else '无'}
最近对话摘要：{conversation_context[:2400] or '这是本线程第一轮'}
用户操作：{'提交回答并请求判题' if request.mode == 'answer' else '请求下一步提示'}
系统确定性校验：{conflict_instruction or '未发现同左边等式冲突，仍需自行核对。'}

要求：
1. assessment 必须是 correct、partial 或 incorrect。
2. 特别核对移项符号、算术和单位；只有确实正确时才能说“对”或表扬该步骤。
3. 如果错误，温和但明确指出哪一步不成立，然后给一个不泄露最终答案的提示。
4. 如果用户是请求提示，只引导下一步，不给最终答案；如果是提交回答，先明确判断再引导修改或继续。
5. reply 应像真人老师一样简短，最后提一个学生可以继续回答的问题。
""".strip()
        selection = self.runtime.selection
        try:
            generated, run = self.runtime.generate_json(prompt, HELP_SCHEMA, max_tokens=450)
        except Exception as error:
            return build_reply(request, cards, mock_model_run(selection.provider, str(error)))

        action = generated.get("canvasAction")
        if action not in CANVAS_ACTIONS:
            action = current_card["canvasAction"]
        assessment = generated.get("assessment", "partial")
        reply_text = _safe_text(generated.get("reply"), current_card["hint"], 1000)
        if conflict:
            assessment = "incorrect"
            reply_text = (
                f"这里需要再核对一下：你写的 {conflict[0]} 与前一步推导不一致。"
                "先别继续除法，请重新检查移项后的符号和右边的计算，你能重算这一行吗？"
            )
            action = current_card["canvasAction"]
        return TutorReply(
            reply=reply_text,
            guideContext={
                "assessment": assessment,
                "stuckAt": _safe_text(generated.get("stuckAt"), current_card["stuckAt"], 300),
                "knowledge": _safe_string_list(generated.get("knowledge"), current_card["knowledge"]),
                "hint": _safe_text(generated.get("hint"), current_card["hint"], 500),
                "question": _safe_text(generated.get("question"), current_card["question"], 500),
            },
            nextHintLevel=min(request.hintLevel + 1, 3),
            canvasAction=action,
            source="model-generated",
            modelRun=run,
        )
