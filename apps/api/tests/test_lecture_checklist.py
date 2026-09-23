from __future__ import annotations

import unittest

from application.services.lecture_checklist import aggregate_lecture_checklist


class LectureChecklistAggregationTests(unittest.TestCase):
    def test_uses_latest_attempt_and_teacher_overturn_without_dropping_unknown(self) -> None:
        items = aggregate_lecture_checklist(
            members=[
                {"learnerId": "a", "displayName": "小安"},
                {"learnerId": "b", "displayName": "小北"},
                {"learnerId": "c", "displayName": "小陈"},
            ],
            questions=[
                {"questionId": "q1", "questionOrder": 0, "title": "第一题"},
                {"questionId": "q2", "questionOrder": 1, "title": "第二题"},
            ],
            attempts=[
                {"learner_id": "a", "question_id": "q1", "assessment": "incorrect", "created_at": 1, "attempt_id": "old"},
                {"learner_id": "a", "question_id": "q1", "assessment": "correct", "created_at": 2, "attempt_id": "new"},
                {"learner_id": "b", "question_id": "q1", "assessment": "incorrect", "created_at": 1, "attempt_id": "b-q1"},
                {"learner_id": "c", "question_id": "q2", "assessment": "incorrect", "created_at": 1, "attempt_id": "c-q2"},
            ],
            reviews=[
                {"learner_id": "b", "question_id": "q1", "action": "overturned", "corrected_assessment": "correct", "created_at": 3, "event_id": "review-1"},
            ],
            mistakes=[],
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["questionId"], "q2")
        self.assertEqual(items[0]["students"][0]["errorReason"], "unknown")
        self.assertEqual(items[0]["errorRate"], 1.0)

    def test_sorts_by_involved_students_then_rate_then_question_order(self) -> None:
        items = aggregate_lecture_checklist(
            members=[{"learnerId": "a"}, {"learnerId": "b"}],
            questions=[
                {"questionId": "q1", "questionOrder": 0, "title": "一"},
                {"questionId": "q2", "questionOrder": 1, "title": "二"},
            ],
            attempts=[
                {"learner_id": "a", "question_id": "q1", "assessment": "incorrect", "created_at": 1, "attempt_id": "1"},
                {"learner_id": "b", "question_id": "q1", "assessment": "incorrect", "created_at": 1, "attempt_id": "2"},
                {"learner_id": "a", "question_id": "q2", "assessment": "incorrect", "created_at": 1, "attempt_id": "3"},
            ],
            reviews=[],
            mistakes=[],
            limit=1,
        )
        self.assertEqual([item["questionId"] for item in items], ["q1"])
        self.assertEqual(items[0]["involvedStudentCount"], 2)

    def test_returns_attribution_source_and_mistake_evidence_ref(self) -> None:
        items = aggregate_lecture_checklist(
            members=[{"learnerId": "a"}],
            questions=[{"questionId": "q1", "questionOrder": 0, "title": "一"}],
            attempts=[{"learner_id": "a", "question_id": "q1", "assessment": "incorrect", "created_at": 1, "attempt_id": "a-q1"}],
            reviews=[],
            mistakes=[{
                "learner_id": "a", "mistake_id": "mistake-a-q1",
                "question_payload_json": {"question": {"id": "q1"}},
                "ai_error_reason": "calculation", "error_reason": "concept",
            }],
        )
        student = items[0]["students"][0]
        self.assertEqual(student["attributionSource"], "ai")
        self.assertEqual(student["mistakeEvidenceRef"], "mistake:mistake-a-q1:a")
        self.assertIn("mistake:mistake-a-q1:a", student["evidenceRefs"])
