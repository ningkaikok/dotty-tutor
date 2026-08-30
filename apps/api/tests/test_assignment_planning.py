from __future__ import annotations

import unittest

from domain.assignment_planning import (
    aggregate_class_mastery,
    aggregate_error_reason_stats,
    deterministic_goals,
    source_fingerprint,
)


class AssignmentPlanningDomainTests(unittest.TestCase):
    def test_mastery_groups_publication_rows_by_normalized_topic_and_excludes_unobserved(self) -> None:
        members = [{"learnerId": "a"}, {"learnerId": "b"}, {"learnerId": "c"}]
        states = [
            {"learnerId": "a", "publicationId": "p1", "knowledgePointId": "p1-k", "normalizedName": " 一次函数 ", "name": "一次函数", "score": 0.2, "evidenceCount": 1},
            {"learnerId": "a", "publicationId": "p2", "knowledgePointId": "p2-k", "normalizedName": "一次函数", "name": "一次函数", "score": 0.6, "evidenceCount": 1},
            {"learnerId": "outsider", "normalizedName": "一次函数", "score": 0, "evidenceCount": 99},
            {"learnerId": "b", "normalizedName": "一次函数", "score": 0, "evidenceCount": 0},
        ]
        result = aggregate_class_mastery(members, states)["一次函数"]
        self.assertEqual(result["observedStudentCount"], 1)
        self.assertEqual(result["notObservedStudentCount"], 2)
        self.assertEqual(result["notObserved"], 2)
        self.assertEqual(result["averageScore"], 0.4)

    def test_error_stats_keep_three_attributions_and_effective_prefers_ai(self) -> None:
        rows = [
            {"learnerId": "a", "knowledgePoint": "一次函数", "status": "unmastered", "errorReason": "concept", "aiErrorReason": "calculation"},
            {"learnerId": "b", "knowledgePoint": "一次函数", "status": "unmastered", "errorReason": "concept", "aiErrorReason": "unknown"},
            {"learnerId": "c", "knowledgePoint": "一次函数", "status": "pending_confirmation", "errorReason": "reading"},
        ]
        result = aggregate_error_reason_stats([{"learnerId": "a"}, {"learnerId": "b"}, {"learnerId": "c"}], rows)["一次函数"]
        self.assertEqual(result["selfReported"], {"concept": 2})
        self.assertEqual(result["aiAttributed"], {"calculation": 1})
        self.assertEqual(result["effective"], {"calculation": 1, "concept": 1})

    def test_goal_order_and_fingerprint_are_stable(self) -> None:
        mastery = {
            "强项": {"planningTopicKey": "强项", "topic": "强项", "observedStudentCount": 1, "averageScore": 0.9},
            "弱项": {"planningTopicKey": "弱项", "topic": "弱项", "observedStudentCount": 1, "averageScore": 0.2},
        }
        coverage = {key: {"planningTopicKey": key, "topic": key, "questionCount": 1} for key in mastery}
        goals = deterministic_goals(mastery, {}, coverage)
        self.assertEqual([goal["planningTopicKey"] for goal in goals], ["弱项", "强项"])
        self.assertEqual(source_fingerprint({"b": 1, "a": 2}), source_fingerprint({"a": 2, "b": 1}))


if __name__ == "__main__":
    unittest.main()
