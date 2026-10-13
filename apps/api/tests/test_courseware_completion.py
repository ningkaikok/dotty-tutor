"""User scenarios for read-only physical-page completion suggestions."""
from __future__ import annotations

import unittest

from application.services.courseware_completion import completion_report


class CoursewareCompletionScenarios(unittest.TestCase):
    def _report(self, *, end: int = 12, generated: list[int] | None = None,
                reviewed: list[int] | None = None, planned: list[int] | None = None,
                blocked: list[int] | None = None, plan: dict | None = None,
                active: bool = False, batch_size: int = 5) -> dict:
        return completion_report(
            {"chapterId": "chapter", "recordVersion": 7},
            {"sourceRevisionId": "revision", "pageStart": 1, "pageEnd": end},
            {"generatedPages": generated or [], "reviewedPages": reviewed or [],
             "plannedPages": planned or [], "publishedPages": []},
            plan=plan, blocked_pages=blocked or [], active_generation=active,
            max_pages_per_batch=batch_size,
        )

    def test_user_prepares_an_unplanned_unit_then_receives_bounded_contiguous_page_ranges(self) -> None:
        # Given twelve ready physical source pages with no plan
        # When reading completion suggestions with the default five-page limit
        report = self._report()
        # Then each suggested batch is a real contiguous range and only proposes preparation
        self.assertEqual(report["batches"], [
            {"pageStart": 1, "pageEnd": 5, "sourcePages": [1, 2, 3, 4, 5]},
            {"pageStart": 6, "pageEnd": 10, "sourcePages": [6, 7, 8, 9, 10]},
            {"pageStart": 11, "pageEnd": 12, "sourcePages": [11, 12]},
        ])
        self.assertEqual(report["expectedRecordVersion"], 7)
        self.assertTrue(report["canCreatePlan"])
        self.assertTrue(report["requiresHumanReview"])

    def test_user_opens_partial_unit_then_generated_planned_and_unreadable_pages_are_not_suggested_again(self) -> None:
        # Given completed pages 1/5, an existing plan for 2/3, and blocked pages 4/7
        plan = {"sourceRevisionId": "revision", "pageStart": 2, "pageEnd": 3,
                "status": "approved", "generationStatus": "completed", "planVersion": 2, "recordVersion": 4}
        # When reading the next missing ranges
        report = self._report(end=9, generated=[1, 5], reviewed=[5], planned=[2, 3], blocked=[4, 7], plan=plan)
        # Then gaps split the ranges, approved planning remains protected, and page 1 still needs review
        self.assertEqual(report["batches"], [
            {"pageStart": 6, "pageEnd": 6, "sourcePages": [6]},
            {"pageStart": 8, "pageEnd": 9, "sourcePages": [8, 9]},
        ])
        self.assertEqual(report["pendingGenerationPages"], [2, 3])
        self.assertEqual(report["pendingReviewPages"], [1])
        self.assertEqual(report["blockedPages"], [4, 7])
        self.assertFalse(report["canCreatePlan"])
        self.assertEqual(report["action"], "continue_plan")

    def test_user_opens_a_unit_while_generation_runs_then_suggestions_cannot_replace_the_active_plan(self) -> None:
        # Given queued work whose output has not yet reached physical-page coverage
        # When reading completion preparation
        report = self._report(active=True)
        # Then the operator must wait even though page suggestions can be inspected
        self.assertFalse(report["canCreatePlan"])
        self.assertEqual(report["action"], "wait_generation")

    def test_user_changes_source_then_old_planned_pages_do_not_hide_new_source_gaps(self) -> None:
        # Given a plan pinned to the previous source revision
        plan = {"sourceRevisionId": "old", "pageStart": 1, "pageEnd": 2,
                "status": "approved", "generationStatus": "completed", "planVersion": 1, "recordVersion": 3}
        # When current-source coverage contains no matching planned pages
        report = self._report(end=2, plan=plan)
        # Then the old plan is explicitly stale and the new source remains identifiable
        self.assertTrue(report["existingPlan"]["stale"])
        self.assertEqual(report["sourceRevisionId"], "revision")
        self.assertEqual(report["alreadyPlannedPages"], [])
        self.assertEqual(report["batches"][0]["sourcePages"], [1, 2])

    def test_user_has_only_generated_pages_then_missing_human_review_is_the_next_step(self) -> None:
        # Given all three pages have content but only two have teacher approval
        # When reading preparation status
        report = self._report(end=3, generated=[1, 2, 3], reviewed=[1, 2])
        # Then no regeneration is proposed and the human review gap stays visible
        self.assertEqual(report["batches"], [])
        self.assertEqual(report["pendingReviewPages"], [3])
        self.assertEqual(report["action"], "review_generated")

    def test_user_sets_an_invalid_batch_limit_then_preparation_is_rejected(self) -> None:
        # Given a request outside the supported 1–20 physical-page bound
        # When preparing suggestions, Then no oversized batch is produced
        for limit in (0, 21):
            with self.assertRaises(ValueError):
                self._report(batch_size=limit)
