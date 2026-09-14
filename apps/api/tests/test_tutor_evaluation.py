import unittest

from evaluation.tutor.cases import CASES, DIMENSIONS
from evaluation.tutor.metrics import (
    evidence_hit,
    image_grounding_safe,
    latency_cost_summary,
    prompt_escalation_safe,
    socratic_safe,
    tool_policy_safe,
)
from evaluation.tutor.runner import check_cases


class TutorEvaluationTests(unittest.TestCase):
    def test_corpus_has_all_dimensions(self) -> None:
        report = check_cases()
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["dimensions"], list(DIMENSIONS))
        self.assertEqual(report["cases"], len(CASES))

    def test_quality_metrics_are_deterministic(self) -> None:
        self.assertEqual(evidence_hit({"evidence": "第 2 步"}, {"evidence": "第 2 步的负号"}), 1.0)
        self.assertEqual(socratic_safe({"must_ask": True, "must_not_reveal": True}, {"question": "哪一步最不确定？"}), 1.0)
        self.assertEqual(prompt_escalation_safe({"hint_level": 2, "retry_limit": 1}, {"hintLevel": 2, "retryCount": 1}), 1.0)
        self.assertEqual(image_grounding_safe({"min_confidence": 0.8, "requires_region": True}, {"confidence": 0.9, "evidenceRegions": [{"x": 0}]}), 1.0)
        self.assertEqual(tool_policy_safe({"must_deny": True}, {"decision": "deny"}), 1.0)
        self.assertEqual(latency_cost_summary([{"latencyMs": 100, "costUsd": 0.01}, {"latencyMs": 300, "costUsd": 0.02}])["costTotalUsd"], 0.03)
