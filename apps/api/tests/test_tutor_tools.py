import unittest

from domain.tutoring.tools import validate_tool_proposal


class TutorToolPolicyTests(unittest.TestCase):
    def test_denies_unconfirmed_evidence_and_allows_confirmed_explanation(self) -> None:
        proposal = {
            "name": "record_evidence",
            "reason": "保存这次步骤",
            "evidenceRefs": ["input-1"],
        }
        denied = validate_tool_proposal(
            proposal,
            stage="diagnose",
            input_item={"status": "needs_confirmation", "content": "x"},
            action={"assessment": "partial"},
        )
        self.assertEqual(denied.decision, "deny")
        allowed = validate_tool_proposal(
            {"name": "explain_mistake", "reason": "解释已确认步骤", "evidenceRefs": ["input-1"]},
            stage="explain",
            input_item={"status": "confirmed", "content": "x"},
            action={"assessment": "partial"},
        )
        self.assertEqual(allowed.decision, "allow")

    def test_schedule_review_requires_verify_and_correct(self) -> None:
        proposal = {"name": "schedule_review", "reason": "安排复习", "evidenceRefs": ["turn-1"]}
        self.assertEqual(
            validate_tool_proposal(proposal, stage="practice", input_item=None, action={"assessment": "correct"}).decision,
            "deny",
        )
        self.assertEqual(
            validate_tool_proposal(proposal, stage="verify", input_item=None, action={"assessment": "correct"}).decision,
            "allow",
        )
