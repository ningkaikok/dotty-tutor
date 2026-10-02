"""Behavior checks for the human rating handoff and fail-closed summary."""

from __future__ import annotations

import unittest

from evaluation.benchmark.blind_review import (
    prepare_packet,
    render_html,
    summarize_ratings,
)


def _case(case_id: str, dimension: str) -> dict:
    return {
        "caseId": case_id,
        "taskDimension": dimension,
        "input": {"prompt": f"question {case_id}"},
        "expected": {"answer": "correct"},
        "rubric": {"must": "explain correctly"},
        "evaluationEligible": True,
    }


def _report(cases: list[dict]) -> dict:
    return {
        "status": "completed",
        "baselineModel": "qwen2.5:3b",
        "candidateModel": "qwen2.5:7b",
        "datasetSha256": "dataset-sha",
        "results": [
            {
                **{key: case[key] for key in ("caseId", "taskDimension", "input", "expected", "rubric")},
                "baseline": {"status": "success", "output": {"answer": "baseline"}, "durationMs": 100},
                "candidate": {"status": "success", "output": {"answer": "candidate"}, "durationMs": 200},
            }
            for case in cases
        ],
    }


class BlindReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cases = [
            _case("tutor-1", "error_localization"),
            _case("tutor-2", "socratic_question"),
            _case("system-1", "latency_cost"),
        ]
        self.packet, self.key = prepare_packet(
            _report(self.cases), self.cases, report_sha256="report-sha", seed=42
        )

    def test_user_receives_model_blind_pair_without_timing_or_identity(self) -> None:
        """Given paired outputs, when preparing review, then labels and timing stay hidden."""
        self.assertEqual(self.packet["caseCount"], 3)
        self.assertEqual(len(self.key["assignments"]), 3)
        packet_text = str(self.packet)
        html = render_html(self.packet)
        for hidden in ("qwen2.5:3b", "qwen2.5:7b", "durationMs", "baselineModel", "candidateModel"):
            self.assertNotIn(hidden, packet_text)
            self.assertNotIn(hidden, html)
        self.assertIn("陪练模型匿名配对审核", html)

    def test_user_review_is_decoded_and_tutor_results_stay_separate(self) -> None:
        """Given complete blinded ratings, when decoding, then tutor and routing stay separate."""
        ratings = []
        for assignment in self.key["assignments"]:
            tutor = assignment["caseId"].startswith("tutor")
            preferred_arm = "candidate" if tutor else "baseline"
            ratings.append({
                "caseId": assignment["caseId"],
                "aPass": assignment["A"] == preferred_arm,
                "bPass": assignment["B"] == preferred_arm,
                "preferred": "A" if assignment["A"] == preferred_arm else "B",
                "reason": "Checked against the case rubric.",
            })
        review = {
            "packetId": self.packet["packetId"],
            "reviewerId": "human-reviewer-1",
            "reviewedAt": "2026-09-29T08:00:00Z",
            "ratings": ratings,
        }
        summary = summarize_ratings(self.packet, self.key, review)
        tutor = summary["cohorts"]["tutorText"]
        system = summary["cohorts"]["systemRouting"]
        self.assertEqual(tutor["cases"], 2)
        self.assertEqual(tutor["pairedPassDifference"]["candidateSuccesses"], 2)
        self.assertEqual(tutor["preferenceCounts"]["candidate"], 2)
        self.assertEqual(system["cases"], 1)
        self.assertEqual(system["preferenceCounts"]["baseline"], 1)

    def test_incomplete_or_tampered_review_fails_closed(self) -> None:
        """Given missing judgments or altered source, when summarizing, then no ranking is emitted."""
        review = {
            "packetId": self.packet["packetId"],
            "reviewerId": "human-reviewer-1",
            "reviewedAt": "2026-09-29T08:00:00Z",
            "ratings": [{"caseId": "tutor-1", "aPass": True, "bPass": None, "preferred": "A", "reason": ""}],
        }
        with self.assertRaises(ValueError):
            summarize_ratings(self.packet, self.key, review)
        changed_report = _report(self.cases)
        changed_report["results"][0]["expected"] = {"answer": "tampered"}
        with self.assertRaises(ValueError):
            prepare_packet(changed_report, self.cases, report_sha256="report-sha", seed=42)
        complete = {
            "packetId": self.packet["packetId"],
            "reviewerId": "human-reviewer-1",
            "reviewedAt": "2026-09-29T08:00:00Z",
            "ratings": [
                {"caseId": case["caseId"], "aPass": True, "bPass": False, "preferred": "A", "reason": "Rubric checked."}
                for case in self.packet["cases"]
            ],
        }
        altered_packet = {**self.packet, "caseCount": 2}
        with self.assertRaises(ValueError):
            summarize_ratings(altered_packet, self.key, complete)


if __name__ == "__main__":
    unittest.main()
