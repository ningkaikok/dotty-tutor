from __future__ import annotations

import unittest
from types import SimpleNamespace

from application.services.stateful_tutor import StatefulTutor
from domain.questions.contracts import HELP_SCHEMA, HelpRequest, TutorReply
from domain.tutoring.checks import build_reply
from domain.tutoring.turn_plan import (
    build_tutor_turn_plan,
    infer_student_intent,
    normalize_misconception,
    resolve_error_strategy,
    select_teaching_action,
    teaching_strategy_context,
)
from infrastructure.runtime.contracts import PromptParts


class _RepeatingEngine:
    def __init__(self, reply: TutorReply) -> None:
        self.reply_value = reply
        self.calls = 0

    def reply(self, request: HelpRequest, *, conversation_context: str = "") -> TutorReply:
        self.calls += 1
        return self.reply_value


class _GeneratedRuntime:
    def __init__(self, generated: dict) -> None:
        self.selection = SimpleNamespace(provider="codex", model="test-model")
        self.generated = generated
        self.calls = 0
        self.prompts: list[str] = []
        self.prompt_parts: list[PromptParts | None] = []

    def generate_json(self, prompt: str | PromptParts, schema: dict, max_tokens: int = 450):
        self.calls += 1
        # 真实 runtime 接受 PromptParts；这里落成整段文本，让断言继续检查
        # 学生真正看到的提示词，同时另存切分供前缀顺序断言使用。
        self.prompt_parts.append(prompt if isinstance(prompt, PromptParts) else None)
        self.prompts.append(prompt.text if isinstance(prompt, PromptParts) else prompt)
        return self.generated, {
            "requestedProvider": "codex",
            "provider": "codex",
            "model": "test-model",
            "fallback": False,
        }


class TutorTurnPlanTests(unittest.TestCase):
    @staticmethod
    def _stateful_inputs(content: str = "为什么要这样做") -> dict:
        return {
            "mistake": {
                "errorReason": "concept",
                "knowledgePoint": "有理数比较",
                "questionPayload": {
                    "question": {
                        "id": "q-1",
                        "questionType": "short-answer",
                        "prompt": "比较 -2 和 1 的大小",
                        "givens": ["-2", "1"],
                        "knowledgePoint": "有理数比较",
                    },
                    "lessonSteps": [],
                },
                "guideCards": [{
                    "level": 0,
                    "stuckAt": "需要先比较两个数",
                    "knowledge": ["有理数比较"],
                    "hint": "在数轴上定位",
                    "question": "哪个数在右边？",
                    "canvasAction": "show-base",
                }],
            },
            "thread": {"stage": "explain", "summary": ""},
            "recent_messages": [],
            "request": SimpleNamespace(
                content=content,
                hintLevel=0,
                mode="help",
                interactionResult={},
            ),
        }

    def test_model_misconception_is_returned_in_same_bounded_call(self) -> None:
        runtime = _GeneratedRuntime({
            "assessment": "correct",
            "reply": "你提到了数轴方向，能指出两个点的位置吗？",
            "stuckAt": "可能混淆了数轴方向",
            "knowledge": ["数轴"],
            "hint": "先标点",
            "question": "哪个点在右边？",
            "canvasAction": "show-base",
            "misconception": {
                "hypothesis": "学生可能把负数看作更大",
                "evidence": "学生问为什么要这样比较",
                "confidence": 0.4,
                "needsConfirmation": False,
            },
        })
        result = StatefulTutor(runtime=runtime).reply(**self._stateful_inputs())
        plan = result["action"]["tutorTurnPlan"]

        self.assertEqual(runtime.calls, 1)
        self.assertIn("学生意图：request-explanation", runtime.prompts[0])
        self.assertIn("唯一教学动作：contrast-concepts", runtime.prompts[0])
        self.assertTrue(plan["misconception"]["needsConfirmation"])
        self.assertEqual(plan["teachingAction"], "ask-justification")
        self.assertEqual(plan["audit"]["generationTeachingAction"], "contrast-concepts")
        self.assertTrue(plan["audit"]["teachingActionAdjusted"])
        # 模型说 correct 仍只是 guided，不能推进到 practice。
        self.assertEqual(result["stage"], "explain")
        self.assertEqual(plan["audit"]["assessmentAuthority"], "guided")
        self.assertIn("还不能确定", result["reply"].reply)
        self.assertNotIn("你提到了数轴方向", result["reply"].reply)
    def test_eight_student_intents_are_stable(self) -> None:
        cases = (
            ("submit-answer", "answer", "我选 A", {}),
            ("confirm-ready", "answer", "准备好了", {}),
            ("request-hint", "help", "给我一点提示", {}),
            ("request-explanation", "help", "为什么要这样做", {}),
            ("check-step", "answer", "帮我检查这一步", {}),
            ("challenge-answer", "help", "我不认同标准答案", {}),
            ("request-example", "help", "能举个例子吗", {}),
            ("express-confusion", "help", "我完全看不懂", {}),
            ("off-topic", "help", "今天天气怎么样", {}),
        )
        for expected, mode, content, interaction in cases:
            with self.subTest(expected=expected):
                intent = infer_student_intent(
                    mode=mode,
                    content=content,
                    interaction_result=interaction,
                )
                self.assertEqual(intent["id"], expected)
                self.assertGreater(intent["confidence"], 0)
                self.assertTrue(intent["evidence"])

    def test_structured_answer_has_priority_over_spoken_keywords(self) -> None:
        intent = infer_student_intent(
            mode="help",
            content="标准答案是不是错了",
            interaction_result={"selectedOptions": ["B"]},
        )
        self.assertEqual(intent["id"], "submit-answer")
        self.assertEqual(intent["evidence"], ["interaction-result"])

    def test_ready_confirmation_is_a_deterministic_practice_transition(self) -> None:
        inputs = self._stateful_inputs("准备好了")
        inputs["request"].mode = "answer"
        result = StatefulTutor(runtime=SimpleNamespace(
            selection=SimpleNamespace(provider="mock", model="demo")
        )).reply(**inputs)
        self.assertEqual(result["stage"], "practice")
        self.assertIn("变式练习", result["reply"].reply)
        self.assertNotIn("卡在", result["reply"].reply)
        self.assertEqual(result["action"]["tutorTurnPlan"]["intent"]["id"], "confirm-ready")
        self.assertEqual(result["action"]["tutorTurnPlan"]["teachingAction"], "generate-micro-practice")
        self.assertEqual(result["action"]["deduplication"]["status"], "deterministic-ready-transition")

    def test_help_schema_is_strict_at_every_new_object_level(self) -> None:
        self.assertEqual(set(HELP_SCHEMA["properties"]), set(HELP_SCHEMA["required"]))
        diagnosis = HELP_SCHEMA["properties"]["misconception"]
        self.assertFalse(diagnosis["additionalProperties"])
        self.assertEqual(set(diagnosis["properties"]), set(diagnosis["required"]))

    def test_misconception_without_evidence_or_confidence_requires_confirmation(self) -> None:
        missing_evidence = normalize_misconception({
            "hypothesis": "学生混淆了相反数与绝对值",
            "evidence": "",
            "confidence": 0.9,
            "needsConfirmation": False,
        })
        low_confidence = normalize_misconception({
            "hypothesis": "学生可能漏看单位",
            "evidence": "学生只写了数值 7",
            "confidence": 0.4,
            "needsConfirmation": False,
        })
        self.assertTrue(missing_evidence["needsConfirmation"])
        self.assertTrue(low_confidence["needsConfirmation"])

    def test_misconception_category_is_normalized(self) -> None:
        base = {
            "hypothesis": "学生混淆了概念",
            "evidence": "学生说这个概念不明白",
            "confidence": 0.9,
            "needsConfirmation": False,
        }
        legal = normalize_misconception({**base, "category": "concept"})
        illegal = normalize_misconception({**base, "category": "not-a-category"})
        missing = normalize_misconception(base)

        self.assertEqual(legal["category"], "concept")
        self.assertEqual(illegal["category"], "unknown")
        self.assertEqual(missing["category"], "unknown")
        self.assertFalse(illegal["needsConfirmation"])
        self.assertFalse(missing["needsConfirmation"])

    def test_strategy_prefers_gated_ai_attribution(self) -> None:
        plan = build_tutor_turn_plan(
            error_reason="concept",
            current_stage="explain",
            mode="help",
            assessment="partial",
            reply_source="model-generated",
            misconception={
                "hypothesis": "学生漏掉了计算步骤",
                "evidence": "学生说这里少了一步计算",
                "confidence": 0.9,
                "needsConfirmation": False,
                "category": "missing_step",
            },
        )

        self.assertEqual(plan["errorStrategy"]["reason"], "missing_step")
        self.assertEqual(plan["errorStrategy"]["id"], "step-completion")
        self.assertEqual(plan["errorStrategy"]["source"], "ai")

    def test_strategy_falls_back_to_self_assessment_when_ai_is_unconfirmed(self) -> None:
        plan = build_tutor_turn_plan(
            error_reason="reading",
            current_stage="explain",
            mode="help",
            assessment="partial",
            reply_source="model-generated",
            misconception={
                "hypothesis": "学生可能漏看条件",
                "evidence": "学生说不确定",
                "confidence": 0.4,
                "needsConfirmation": True,
                "category": "calculation",
            },
        )

        self.assertEqual(plan["errorStrategy"]["reason"], "reading")
        self.assertEqual(plan["errorStrategy"]["source"], "self")

    def test_unknown_or_illegal_gated_category_keeps_self_assessment(self) -> None:
        base = {
            "hypothesis": "学生可能混淆了当前步骤",
            "evidence": "学生说不知道为什么要这样比较",
            "evidenceMatched": True,
            "confidence": 0.9,
            "needsConfirmation": False,
        }
        for category in ("unknown", "not-a-category"):
            with self.subTest(category=category):
                reason, source = resolve_error_strategy(
                    "calculation",
                    misconception={**base, "category": category},
                )
                self.assertEqual((reason, source), ("calculation", "self"))

    def test_persisted_ai_attribution_precedes_self_assessment(self) -> None:
        reason, source = resolve_error_strategy(
            "concept",
            ai_error_reason="reading",
        )
        self.assertEqual((reason, source), ("reading", "ai"))

        context = teaching_strategy_context(
            "concept", "explain", ai_error_reason="reading"
        )
        self.assertIn("condition-reading", context)
        self.assertNotIn("concept-foundation", context)

    def test_strategy_falls_back_to_unknown_when_both_attributions_are_empty(self) -> None:
        plan = build_tutor_turn_plan(
            error_reason=None,
            current_stage="diagnose",
            mode="help",
            assessment="partial",
            reply_source="model-generated",
        )

        self.assertEqual(plan["errorStrategy"]["reason"], "unknown")
        self.assertEqual(plan["errorStrategy"]["id"], "scaffolded-transfer")
        self.assertEqual(plan["errorStrategy"]["source"], "unknown")

    def test_model_evidence_must_overlap_current_student_input(self) -> None:
        fabricated = normalize_misconception({
            "hypothesis": "学生混淆了绝对值",
            "evidence": "学生把负三写成了正三",
            "confidence": 0.95,
            "needsConfirmation": False,
        }, student_input="我不知道为什么要这样比较")
        grounded = normalize_misconception({
            "hypothesis": "学生不理解当前比较方法",
            "evidence": "学生说不知道为什么要这样比较",
            "confidence": 0.9,
            "needsConfirmation": False,
        }, student_input="我不知道为什么要这样比较")
        self.assertFalse(fabricated["evidenceMatched"])
        self.assertTrue(fabricated["needsConfirmation"])
        self.assertTrue(grounded["evidenceMatched"])
        self.assertFalse(grounded["needsConfirmation"])

    def test_short_student_input_cannot_confirm_misconception(self) -> None:
        diagnosis = normalize_misconception({
            "hypothesis": "没有理解单位变化",
            "evidence": "不会",
            "confidence": 0.95,
            "needsConfirmation": False,
        }, student_input="不会")

        self.assertFalse(diagnosis["evidenceMatched"])
        self.assertTrue(diagnosis["needsConfirmation"])

    def test_misconception_fields_are_cleaned_and_bounded(self) -> None:
        diagnosis = normalize_misconception({
            "hypothesis": " x " * 200,
            "evidence": ["not", "text"],
            "confidence": 3,
            "needsConfirmation": False,
        })
        self.assertLessEqual(len(diagnosis["hypothesis"]), 160)
        self.assertLessEqual(len(diagnosis["evidence"]), 240)
        self.assertEqual(diagnosis["confidence"], 1.0)
        self.assertEqual(diagnosis["evidence"], "")
        self.assertTrue(diagnosis["needsConfirmation"])

    def test_teaching_action_uses_intent_diagnosis_and_domain_rules(self) -> None:
        confirmed = {
            "hypothesis": "混淆正数与绝对值",
            "evidence": "学生说负数绝对值一定更小",
            "confidence": 0.9,
            "needsConfirmation": False,
        }
        self.assertEqual(select_teaching_action(
            intent="challenge-answer", error_reason="concept", current_stage="explain",
            assessment="partial", misconception=confirmed,
        ), "run-self-check")
        self.assertEqual(select_teaching_action(
            intent="request-example", error_reason="unknown", current_stage="explain",
            assessment="partial", misconception=confirmed,
        ), "show-micro-example")
        self.assertEqual(select_teaching_action(
            intent="submit-answer", error_reason="calculation", current_stage="diagnose",
            assessment="incorrect", misconception=confirmed,
        ), "inspect-first-error")
        self.assertEqual(select_teaching_action(
            intent="request-hint", error_reason="concept", current_stage="explain",
            assessment="partial", misconception=confirmed,
        ), "contrast-concepts")

    def test_low_confidence_diagnosis_selects_confirmation_action(self) -> None:
        action = select_teaching_action(
            intent="request-hint",
            error_reason="concept",
            current_stage="explain",
            assessment="partial",
            misconception={
                "hypothesis": "学生可能混淆概念",
                "evidence": "只写了一个数",
                "confidence": 0.3,
                "needsConfirmation": False,
            },
        )
        self.assertEqual(action, "ask-justification")

    def test_guided_assessment_cannot_replace_locked_teaching_action(self) -> None:
        plan = build_tutor_turn_plan(
            error_reason="concept",
            current_stage="explain",
            mode="help",
            assessment="correct",
            reply_source="model-generated",
            student_intent={
                "id": "request-explanation", "confidence": 0.9,
                "evidence": ["phrase:为什么"],
            },
            misconception={
                "hypothesis": "学生混淆了正负数顺序",
                "evidence": "学生说为什么负数更小",
                "evidenceMatched": True,
                "confidence": 0.9,
                "needsConfirmation": False,
            },
            generation_teaching_action="contrast-concepts",
        )
        self.assertEqual(plan["teachingAction"], "contrast-concepts")
        self.assertFalse(plan["audit"]["teachingActionAdjusted"])

    def test_mock_reply_has_safe_unconfirmed_misconception(self) -> None:
        reply = build_reply(HelpRequest(questionId="q", mode="help"))
        self.assertTrue(reply.guideContext["misconception"]["needsConfirmation"])
        self.assertEqual(reply.guideContext["misconception"]["confidence"], 0)

    def test_all_confirmed_error_reasons_have_stable_strategies(self) -> None:
        expected = {
            "concept": "concept-foundation", "reading": "condition-reading",
            "calculation": "parallel-calculation", "missing_step": "step-completion",
            "unknown": "scaffolded-transfer", "careless": "self-check",
        }
        for reason, strategy in expected.items():
            with self.subTest(reason=reason):
                plan = build_tutor_turn_plan(
                    error_reason=reason, current_stage="diagnose", mode="help",
                    assessment="partial", reply_source="model-generated",
                )
                self.assertEqual(plan["errorStrategy"]["id"], strategy)
                self.assertEqual(plan["suggestedStage"], "explain")
                self.assertFalse(plan["shouldRevealAnswer"])

    def test_deterministic_assessment_controls_stage_and_reveal_boundary(self) -> None:
        plan = build_tutor_turn_plan(
            error_reason="calculation", current_stage="practice", mode="answer",
            assessment="incorrect", reply_source="answer-check",
            assessment_authority="deterministic",
        )
        self.assertEqual(plan["suggestedStage"], "practice")
        self.assertTrue(plan["shouldRevealAnswer"])
        self.assertFalse(plan["audit"]["modelMayOverride"])

    def test_generated_positive_assessment_cannot_advance_stage(self) -> None:
        plan = build_tutor_turn_plan(
            error_reason="concept", current_stage="explain", mode="answer",
            assessment="correct", reply_source="model-generated",
        )
        self.assertEqual(plan["suggestedStage"], "explain")
        self.assertEqual(plan["audit"]["assessmentAuthority"], "guided")

    def test_guided_follow_up_cannot_move_practice_or_verify_backwards(self) -> None:
        for stage in ("practice", "verify"):
            with self.subTest(stage=stage):
                plan = build_tutor_turn_plan(
                    error_reason="calculation", current_stage=stage, mode="answer",
                    assessment="correct", reply_source="model-generated",
                )
                self.assertEqual(plan["suggestedStage"], stage)

    def test_deterministic_variation_completion_moves_practice_to_verify(self) -> None:
        plan = build_tutor_turn_plan(
            error_reason="calculation", current_stage="practice", mode="answer",
            assessment="correct", reply_source="answer-check",
            assessment_authority="deterministic",
        )
        self.assertEqual(plan["suggestedStage"], "verify")

    def test_challenge_answer_stays_in_stage_and_requests_review(self) -> None:
        plan = build_tutor_turn_plan(
            error_reason="concept",
            current_stage="practice",
            mode="answer",
            assessment="correct",
            reply_source="model-generated",
            student_intent={
                "id": "challenge-answer",
                "confidence": 0.95,
                "evidence": ["phrase:标准答案"],
            },
            misconception={
                "hypothesis": "学生质疑答案",
                "evidence": "学生说标准答案不对",
                "confidence": 0.9,
                "needsConfirmation": False,
            },
        )
        self.assertEqual(plan["suggestedStage"], "practice")
        self.assertEqual(plan["teachingAction"], "run-self-check")
        self.assertEqual(plan["audit"]["assessmentAuthority"], "guided")

    def test_source_name_alone_does_not_grant_assessment_authority(self) -> None:
        plan = build_tutor_turn_plan(
            error_reason="concept", current_stage="diagnose", mode="answer",
            assessment="correct", reply_source="answer-check",
        )
        # 可进入解释阶段，但不能像确定性正确答案那样跳到 practice。
        self.assertEqual(plan["suggestedStage"], "explain")
        self.assertFalse(plan["shouldRevealAnswer"])

    def test_confirmed_error_strategy_is_available_to_model_context(self) -> None:
        context = teaching_strategy_context("reading", "explain")
        self.assertIn("condition-reading", context)
        self.assertIn("提取条件", context)
        self.assertIn("explain", context)

    def test_repeat_gets_one_retry_then_explicit_fallback(self) -> None:
        reply = TutorReply(
            reply="请检查题干条件，再试一次。",
            guideContext={"assessment": "partial"}, nextHintLevel=1,
            canvasAction="show-base", source="model-generated",
            modelRun={"provider": "ollama", "model": "demo", "fallback": False},
        )
        engine = _RepeatingEngine(reply)
        result, dedupe = StatefulTutor._deduplicate_reply(
            engine=engine,
            request=HelpRequest(questionId="q", mode="help"), conversation_context="",
            recent_messages=[{"role": "assistant", "content": reply.reply}], reply=reply,
        )
        self.assertEqual(engine.calls, 1)
        self.assertEqual(dedupe["status"], "fallback-after-retry")
        self.assertEqual(dedupe["retryCount"], 1)
        self.assertTrue(dedupe["fallbackUsed"])
        self.assertEqual(result.source, "stored-guide-card")
        self.assertNotEqual(result.reply, reply.reply)

    def test_deterministic_repeat_does_not_retry_or_override_result(self) -> None:
        reply = TutorReply(
            reply="这组选项还不正确。",
            guideContext={"assessment": "incorrect", "assessmentAuthority": "deterministic"},
            nextHintLevel=1, canvasAction="show-base", source="answer-check", modelRun={},
        )
        engine = _RepeatingEngine(reply)
        result, dedupe = StatefulTutor._deduplicate_reply(
            engine=engine, request=HelpRequest(questionId="q", mode="answer"),
            conversation_context="", recent_messages=[{"role": "assistant", "content": reply.reply}], reply=reply,
        )
        self.assertIs(result, reply)
        self.assertEqual(engine.calls, 0)
        self.assertEqual(dedupe["status"], "deterministic-repeat-allowed")

    def test_near_duplicate_wording_is_detected(self) -> None:
        similarity = StatefulTutor._reply_similarity(
            "请先检查题干中的关键条件，然后再试一次。",
            "请先检查题干里的关键条件，再试一次。",
        )
        self.assertGreaterEqual(similarity, 0.76)


if __name__ == "__main__":
    unittest.main()


class PromptPrefixSplitTests(unittest.TestCase):
    """提示词切分的回归资产：稳定段必须真的跨轮不变，否则 Prefix Cache 永不命中。"""

    @staticmethod
    def _runtime() -> _GeneratedRuntime:
        return _GeneratedRuntime({
            "assessment": "partial",
            "reply": "再看看数轴方向。",
            "stuckAt": "数轴方向",
            "knowledge": ["数轴"],
            "hint": "先标点",
            "question": "哪个点在右边？",
            "canvasAction": "show-base",
        })

    def test_prompt_is_passed_as_parts_with_stable_prefix_first(self) -> None:
        runtime = self._runtime()
        StatefulTutor(runtime=runtime).reply(**TutorTurnPlanTests._stateful_inputs())
        parts = runtime.prompt_parts[0]
        self.assertIsNotNone(parts)
        assert parts is not None
        # 缓存只在稳定段是整段提示词的字面前缀时才可能命中。
        self.assertTrue(parts.text.startswith(parts.stable))
        self.assertTrue(parts.text.endswith(parts.dynamic))

    def test_stable_prefix_holds_question_and_requirements_only(self) -> None:
        runtime = self._runtime()
        StatefulTutor(runtime=runtime).reply(
            **TutorTurnPlanTests._stateful_inputs(content="我觉得 -2 更大")
        )
        parts = runtime.prompt_parts[0]
        assert parts is not None
        self.assertIn("比较 -2 和 1 的大小", parts.stable)
        self.assertIn("assessment 必须是 correct、partial 或 incorrect", parts.stable)
        # 本轮状态一律不得进入稳定段，否则它每轮都在变，不再是可复用前缀。
        self.assertNotIn("我觉得 -2 更大", parts.stable)
        self.assertNotIn("当前提示层级", parts.stable)
        self.assertIn("我觉得 -2 更大", parts.dynamic)
        self.assertIn("当前提示层级", parts.dynamic)

    def test_stable_prefix_is_identical_across_turns_of_the_same_question(self) -> None:
        """这是整项改动的核心性质：同一道题换学生输入和提示层级，前缀逐字不变。"""
        runtime = self._runtime()
        tutor = StatefulTutor(runtime=runtime)
        first = TutorTurnPlanTests._stateful_inputs(content="我觉得 -2 更大")
        tutor.reply(**first)
        second = TutorTurnPlanTests._stateful_inputs(content="是不是要看数轴？")
        second["request"].hintLevel = 1
        second["thread"] = {"stage": "explain", "summary": "上一轮学生答错了。"}
        tutor.reply(**second)

        self.assertEqual(len(runtime.prompt_parts), 2)
        stable_first, stable_second = (part.stable for part in runtime.prompt_parts if part)
        self.assertEqual(stable_first, stable_second)
        # 动态段必须真的变了，否则上面的相等是因为两轮输入本来就一样。
        self.assertNotEqual(runtime.prompt_parts[0].dynamic, runtime.prompt_parts[1].dynamic)  # type: ignore[union-attr]
