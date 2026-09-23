from __future__ import annotations

import unittest

from domain.tutoring.tools import normalize_tool_proposals, validate_tool_proposal
from evaluation.tutor.cases import TOOL_SAFETY_CASES
from evaluation.tutor.metrics import tool_safety_summary
from evaluation.tutor.runner import run_tool_safety_cases


class ToolSafetyPolicyTests(unittest.TestCase):
    def test_evidence_must_exist_and_belong_to_learner_when_registry_is_supplied(self) -> None:
        kwargs = {"stage": "explain", "input_item": {"status": "confirmed", "content": "x"}, "action": {"assessment": "partial"}}
        missing = validate_tool_proposal(
            {"name": "explain_mistake", "reason": "x", "evidenceRefs": ["missing"]},
            **kwargs, evidence_registry={"input-a": {"learnerId": "learner-a"}}, evidence_owner="learner-a",
        )
        cross_learner = validate_tool_proposal(
            {"name": "explain_mistake", "reason": "x", "evidenceRefs": ["input-b"]},
            **kwargs, evidence_registry={"input-b": {"learnerId": "learner-b"}}, evidence_owner="learner-a",
        )
        self.assertEqual(missing.decision, "deny")
        self.assertEqual(cross_learner.decision, "deny")

    def test_strict_contract_rejects_unknown_and_extra_fields(self) -> None:
        self.assertEqual(normalize_tool_proposals([{"name": "unknown", "reason": "x", "evidenceRefs": ["a"]}]), [])
        self.assertEqual(normalize_tool_proposals([{"name": "explain_mistake", "reason": "x", "evidenceRefs": ["a"], "masteryScore": 1}]), [])

    def test_safety_metrics_keep_legitimate_and_high_risk_rates_separate(self) -> None:
        result = tool_safety_summary([
            {"decision": "allow", "legitimate": True, "riskClass": "low", "expectedDecision": "allow", "executionStatus": "shadow"},
            {"decision": "deny", "legitimate": False, "riskClass": "high", "expectedDecision": "deny", "executionStatus": "shadow"},
            {"decision": "deny", "legitimate": False, "riskClass": "high", "expectedDecision": "deny", "executionStatus": "shadow"},
        ])
        self.assertEqual(result["legitimateAllowRate"], 1.0)
        self.assertEqual(result["highRiskDenyRate"], 1.0)
        self.assertEqual(result["unauthorizedExecutionCount"], 0)

    def test_adversarial_cases_cover_requested_boundaries(self) -> None:
        ids = {case.case_id for case in TOOL_SAFETY_CASES}
        self.assertTrue({
            "tool-safe-forged-evidence", "tool-safe-cross-learner-evidence", "tool-safe-prompt-jump-stage",
            "tool-safe-prompt-injection-mastery", "tool-safe-prompt-injection-publish", "tool-safe-extra-field", "tool-safe-unknown-tool", "tool-safe-replay",
            "tool-safe-unconfirmed-ocr", "tool-safe-nonverify-review",
        } <= ids)

    def test_offline_runner_denies_all_adversarial_cases_without_execution(self) -> None:
        result = run_tool_safety_cases()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["passed"], result["cases"])
        self.assertEqual(result["metrics"]["unauthorizedExecutionCount"], 0)
