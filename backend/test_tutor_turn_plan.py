from __future__ import annotations

import unittest
from types import SimpleNamespace

from question_contracts import HELP_SCHEMA, HelpRequest, TutorReply
from stateful_tutor import StatefulTutor
from tutor_checks import build_reply
from tutor_turn_plan import (
    build_tutor_turn_plan,
    infer_student_intent,
    normalize_misconception,
    select_teaching_action,
    teaching_strategy_context,
)


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

    def generate_json(self, prompt: str, schema: dict, max_tokens: int = 450):
        self.calls += 1
        self.prompts.append(prompt)
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
        self.assertEqual(plan["suggestedStage"], "explain")
        self.assertTrue(plan["shouldRevealAnswer"])
        self.assertFalse(plan["audit"]["modelMayOverride"])

    def test_generated_positive_assessment_cannot_advance_stage(self) -> None:
        plan = build_tutor_turn_plan(
            error_reason="concept", current_stage="explain", mode="answer",
            assessment="correct", reply_source="model-generated",
        )
        self.assertEqual(plan["suggestedStage"], "explain")
        self.assertEqual(plan["audit"]["assessmentAuthority"], "guided")

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
