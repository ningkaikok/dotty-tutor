from __future__ import annotations

import unittest

from domain.learning.mastery_policy import (
    LEGACY_POLICY_VERSION,
    decide_next_action,
    evaluate_gate,
    resolve_policy,
)
from domain.learning.review_scheduler import follow_up_schedule, initial_schedule


class MasteryPolicyTests(unittest.TestCase):
    def test_objective_profiles_have_versioned_spacing_and_legacy_fallback(self) -> None:
        self.assertEqual(resolve_policy("memory").intervals_days, (1, 3, 7, 14, 30))
        self.assertEqual(resolve_policy("conceptual").intervals_days, (2, 5, 10, 21, 45))
        legacy = resolve_policy(None, None, None)
        self.assertEqual(legacy.policy_version, LEGACY_POLICY_VERSION)
        self.assertEqual(legacy.gate_mode, "legacy")

    def test_typed_policy_rejects_mismatched_gate_and_unknown_version(self) -> None:
        with self.assertRaisesRegex(ValueError, "gateMode"):
            resolve_policy("procedural", "qualitative", "mastery-policy-v1")
        with self.assertRaisesRegex(ValueError, "policyVersion"):
            resolve_policy("procedural", "quantitative", "mastery-policy-v2")
        with self.assertRaisesRegex(ValueError, "objectiveType"):
            resolve_policy("unknown", "quantitative", LEGACY_POLICY_VERSION)

    def test_legacy_policy_requires_the_registered_legacy_version(self) -> None:
        legacy = resolve_policy("unknown", "legacy", LEGACY_POLICY_VERSION)
        self.assertEqual(legacy.profile, "unknown:legacy")
        with self.assertRaisesRegex(ValueError, "policyVersion"):
            resolve_policy("unknown", "legacy", "mastery-policy-v1")

    def test_quantitative_gate_needs_accuracy_and_two_evidence_rows(self) -> None:
        policy = resolve_policy("procedural")
        self.assertEqual(
            evaluate_gate(policy, accuracy=1.0, evidence_count=1)["nextAction"],
            "stay",
        )
        self.assertEqual(
            evaluate_gate(policy, accuracy=0.9, evidence_count=2)["nextAction"],
            "advance",
        )
        self.assertEqual(
            evaluate_gate(policy, accuracy=0.89, evidence_count=100)["nextAction"],
            "stay",
        )
        self.assertEqual(
            evaluate_gate(policy, accuracy=0.0, evidence_count=0)["nextAction"],
            "needs_review",
        )

    def test_qualitative_gate_requires_rubric_confidence_and_refs(self) -> None:
        policy = resolve_policy("conceptual")
        self.assertEqual(
            evaluate_gate(policy, rubric_passed=True, confidence=0.95, evidence_refs=[])["nextAction"],
            "needs_review",
        )
        decision = evaluate_gate(
            policy,
            rubric_passed=True,
            confidence=0.95,
            evidence_refs=["review:1"],
        )
        self.assertEqual(decision["nextAction"], "advance")

    def test_scheduler_extends_correct_streak_and_retries_wrong_answer(self) -> None:
        policy = resolve_policy("memory")
        first = initial_schedule(policy, base_time=100.0)
        self.assertEqual([item["intervalDays"] for item in first], [1, 3, 7, 14, 30])
        next_task = follow_up_schedule(
            policy,
            base_time=100.0,
            sequence_no=2,
            assessment="correct",
            next_action="advance",
        )
        self.assertEqual(next_task["sequenceNo"], 3)
        self.assertEqual(next_task["intervalDays"], 7)
        retry = follow_up_schedule(
            policy,
            base_time=100.0,
            sequence_no=3,
            assessment="incorrect",
            next_action="stay",
        )
        self.assertEqual(retry["intervalDays"], 1)
        self.assertTrue(retry["supersedeFuture"])

    def test_retries_derive_from_base_policy_without_accumulating_suffixes(self) -> None:
        policy = resolve_policy("procedural")
        first_retry = follow_up_schedule(
            policy,
            base_time=100.0,
            sequence_no=1,
            assessment="incorrect",
            next_action="stay",
            schedule_version=policy.policy_version,
        )
        second_retry = follow_up_schedule(
            policy,
            base_time=200.0,
            sequence_no=1,
            assessment="incorrect",
            next_action="stay",
            schedule_version=first_retry["scheduleVersion"],
        )
        self.assertEqual(first_retry["scheduleVersion"], "mastery-policy-v1:retry-2")
        self.assertEqual(second_retry["scheduleVersion"], "mastery-policy-v1:retry-3")

    def test_correct_stay_keeps_an_evidence_task_after_the_ladder(self) -> None:
        policy = resolve_policy("procedural")
        next_task = follow_up_schedule(
            policy,
            base_time=100.0,
            sequence_no=1,
            assessment="correct",
            next_action="stay",
        )
        self.assertEqual(next_task["sequenceNo"], 2)
        self.assertEqual(next_task["intervalDays"], 3)
        final_task = follow_up_schedule(
            policy,
            base_time=100.0,
            sequence_no=len(policy.intervals_days),
            assessment="correct",
            next_action="stay",
        )
        self.assertEqual(final_task["intervalDays"], policy.intervals_days[-1])

    def test_completed_policy_can_test_out_after_final_interval(self) -> None:
        policy = resolve_policy("memory")
        decision = decide_next_action(
            policy,
            accuracy=1.0,
            evidence_count=2,
            assessment="correct",
            sequence_no=len(policy.intervals_days),
        )
        self.assertEqual(decision["nextAction"], "test_out")
