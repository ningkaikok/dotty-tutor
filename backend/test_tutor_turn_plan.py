from __future__ import annotations

import unittest

from question_contracts import HelpRequest, TutorReply
from stateful_tutor import StatefulTutor
from tutor_turn_plan import build_tutor_turn_plan, teaching_strategy_context


class _RepeatingEngine:
    def __init__(self, reply: TutorReply) -> None:
        self.reply_value = reply
        self.calls = 0

    def reply(self, request: HelpRequest, *, conversation_context: str = "") -> TutorReply:
        self.calls += 1
        return self.reply_value


class TutorTurnPlanTests(unittest.TestCase):
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
