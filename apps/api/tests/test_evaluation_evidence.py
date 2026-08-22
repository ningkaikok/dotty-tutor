"""EvaluationEvidence 判题证据流的验收测试（roadmap T1#8）。

三条硬边界在这里固化：
1. 证据只含客观事实与学生已知信息——标准答案/期望标签绝不出现在任何返回结构里；
2. 计划与消息动作持久化的证据经过白名单净化并携带判据器版本；
3. 空提交的数值题必须走"回条件提取"而不是"检查第一处错误"。
"""

from __future__ import annotations

import json
import unittest

from answer_evaluator import EVALUATOR_VERSION, evaluate_structured_answer
from application.services.tutor_engine import TutorEngine
from domain.tutoring.turn_plan import build_tutor_turn_plan
from infrastructure.runtime.model_runtime import ModelRuntime


def _fill_blank_question() -> dict:
    return {
        "questionType": "fill-blank",
        "blanks": [
            {"id": "b1", "answerType": "text", "correctAnswers": ["4"]},
            {"id": "b2", "answerType": "numeric", "correctAnswers": ["-1"], "tolerance": 0},
            {"id": "b3", "answerType": "text", "correctAnswers": ["平行"]},
        ],
    }


class EvidenceContentTests(unittest.TestCase):
    def test_fill_blank_reports_failed_blank_ids_without_answers(self) -> None:
        result = evaluate_structured_answer(
            _fill_blank_question(),
            "",
            {"blankAnswers": {"b1": "4", "b2": "5", "b3": "平行"}},
        )
        self.assertEqual(result["assessment"], "incorrect")
        evidence = result["evaluationEvidence"]
        self.assertEqual(evidence["strategy"], "fill-blank-parts")
        self.assertEqual(evidence["totalBlanks"], 3)
        self.assertEqual(evidence["matchedCount"], 2)
        self.assertEqual(evidence["failedBlankIds"], ["b2"])
        self.assertEqual(evidence["evaluatorVersion"], EVALUATOR_VERSION)
        # 泄漏检查：整个返回结构里不允许出现正确答案文本。
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("平行", serialized)
        self.assertNotIn('"correctAnswers"', serialized)

    def test_choice_evidence_carries_submitted_labels_and_count_only(self) -> None:
        question = {
            "questionType": "choice",
            "correctAnswer": "C",
        }
        result = evaluate_structured_answer(question, "我选 A", {"selectedOptions": ["A"]})
        evidence = result["evaluationEvidence"]
        self.assertEqual(evidence["strategy"], "choice-set-match")
        self.assertEqual(evidence["submittedLabels"], ["A"])
        self.assertEqual(evidence["expectedCount"], 1)
        serialized = json.dumps(result, ensure_ascii=False)
        # 正确答案 C 不允许出现在证据或回复里（揭示答案由 shouldRevealAnswer 决定）。
        self.assertNotIn('"C"', serialized)

    def test_numeric_evidence_keeps_submitted_raw_and_tolerance(self) -> None:
        question = {
            "questionType": "numeric",
            "answerSpec": {"answerType": "numeric", "expected": "42", "tolerance": 0.5},
        }
        result = evaluate_structured_answer(
            question, "", {"numericAnswer": "41.8"}
        ) or evaluate_structured_answer(question, "41.8", {})
        self.assertEqual(result["evaluationEvidence"]["submittedRaw"], "41.8")
        self.assertEqual(result["evaluationEvidence"]["tolerance"], 0.5)


class TurnPlanEvidenceConsumptionTests(unittest.TestCase):
    def _plan(self, evidence: dict | None) -> dict:
        return build_tutor_turn_plan(
            error_reason="calculation",
            current_stage="practice",
            mode="answer",
            assessment="incorrect",
            reply_source="answer-check",
            assessment_authority="deterministic",
            student_intent={"id": "submit-answer", "confidence": 0.99, "evidence": ["mode:answer"]},
            evaluation_evidence=evidence,
        )

    def test_empty_numeric_submission_routes_back_to_conditions(self) -> None:
        plan = self._plan({"strategy": "numeric-tolerance", "submittedRaw": "", "tolerance": 0})
        self.assertEqual(plan["teachingAction"], "extract-conditions")

    def test_non_empty_submission_keeps_existing_stage_precedence(self) -> None:
        """证据不改变阶段优先级：practice 阶段的既有动作保持不变。"""
        plan = self._plan({"strategy": "numeric-tolerance", "submittedRaw": "-3", "tolerance": 0})
        self.assertEqual(plan["teachingAction"], "complete-step")

    def test_plan_stores_whitelisted_evidence_with_version(self) -> None:
        plan = self._plan({
            "strategy": "fill-blank-parts",
            "totalBlanks": 2,
            "matchedCount": 1,
            "failedBlankIds": ["b2"],
            "evaluatorVersion": EVALUATOR_VERSION,
            # 白名单外字段必须被丢弃
            "correctAnswers": ["secret"],
        })
        stored = plan["evaluationEvidence"]
        self.assertEqual(stored["strategy"], "fill-blank-parts")
        self.assertEqual(stored["evaluatorVersion"], EVALUATOR_VERSION)
        self.assertNotIn("correctAnswers", stored)


class DeterministicReplyEvidenceTests(unittest.TestCase):
    """确定性回复的证据双通道：guideContext 携带证据供计划阶段消费。"""

    def _engine_with(self, question: dict) -> TutorEngine:
        store = {"q-1": {"payload": {"question": question}, "guideCards": []}}
        return TutorEngine(lesson_store=store, runtime=ModelRuntime(), guide_cards=[])

    def test_true_false_reply_carries_evidence(self) -> None:
        from domain.questions.contracts import HelpRequest

        engine = self._engine_with({"questionType": "true-false", "correctAnswer": "正确"})
        request = HelpRequest(questionId="q-1", studentInput="正确", hintLevel=0, mode="answer")
        reply = engine.reply(request)
        evidence = reply.guideContext.get("evaluationEvidence")
        self.assertEqual(evidence["strategy"], "true-false-match")
        self.assertEqual(evidence["submittedLabel"], "正确")

    def test_draw_line_reply_carries_connection_counts(self) -> None:
        from domain.questions.contracts import HelpRequest

        engine = self._engine_with({
            "questionType": "draw-line",
            "interaction": {"requiredConnections": [["A", "B"], ["B", "C"]]},
        })
        request = HelpRequest(
            questionId="q-1",
            studentInput="",
            hintLevel=0,
            mode="answer",
            interactionResult={"connections": [["A", "B"]]},
        )
        reply = engine.reply(request)
        evidence = reply.guideContext.get("evaluationEvidence")
        self.assertEqual(evidence["strategy"], "line-connections")
        self.assertEqual(evidence["submittedCount"], 1)
        self.assertEqual(evidence["requiredCount"], 2)


if __name__ == "__main__":
    unittest.main()
