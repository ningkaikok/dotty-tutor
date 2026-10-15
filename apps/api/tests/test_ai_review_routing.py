"""User scenarios for deterministic routing around advisory AI pre-review verdicts."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from domain.contracts.ai_review import AiReviewModelOutput
from domain.courseware.ai_review import (
    agreement_metrics,
    input_hash,
    is_sampled,
    reviewable_page,
    route_page,
)


def _page(page_id: str, *, version: int = 1, status: str = "pending") -> dict:
    return {"pageId": page_id, "version": version, "reviewStatus": status, "title": page_id,
            "blocks": [{"blockId": "b", "kind": "text", "text": "x"}], "sourceRefs": []}


def _review(verdict: str, *, version: int = 1, at: float = 10.0) -> dict:
    return {"verdict": verdict, "subjectVersion": version, "createdAt": at}


class AiReviewRoutingScenarios(unittest.TestCase):
    def test_user_gets_a_stable_ten_percent_spot_check_draw(self) -> None:
        # Given 2000 page versions and a 10% rate
        draws = [is_sampled("cw", f"p{index}", 1, 0.1) for index in range(2000)]
        # Then roughly one in ten is sampled and repeating the draw never changes it
        self.assertTrue(150 <= sum(draws) <= 250, sum(draws))
        self.assertEqual(draws, [is_sampled("cw", f"p{index}", 1, 0.1) for index in range(2000)])
        # And the 0%/100% bounds behave as switches
        self.assertFalse(is_sampled("cw", "p1", 1, 0))
        self.assertTrue(is_sampled("cw", "p1", 1, 1))

    def test_user_sees_flagged_and_sampled_pages_routed_to_their_queue(self) -> None:
        page = _page("p1")
        # When the AI fails or questions a page, Then the teacher must read it
        self.assertEqual(route_page(page, _review("fail"), sampled=False), "needs_review")
        self.assertEqual(route_page(page, _review("concern"), sampled=False), "needs_review")
        # When the AI passes it, Then it is bulk-confirmable unless the spot check drew it
        self.assertEqual(route_page(page, _review("pass"), sampled=False), "bulk_confirmable")
        self.assertEqual(route_page(page, _review("pass"), sampled=True), "needs_review")
        # When there is no verdict, or the page was edited after the verdict, Then it is not routed
        self.assertEqual(route_page(page, None, sampled=False), "not_reviewed")
        self.assertEqual(route_page(_page("p1", version=2), _review("pass"), sampled=False), "stale")

    def test_user_decision_always_wins_over_the_ai_verdict(self) -> None:
        # Given a page the teacher already decided, When the AI passed or failed it, Then it stays decided
        for status in ("approved", "changes_requested"):
            for verdict in ("pass", "fail"):
                self.assertEqual(route_page(_page("p1", status=status), _review(verdict), sampled=False), "decided")

    def test_user_measures_misses_only_on_pages_a_teacher_actually_checked(self) -> None:
        pages = [_page("missed"), _page("alarm"), _page("agree"), _page("bulk"), _page("edited", version=2)]
        reviews = {"missed": _review("pass"), "alarm": _review("fail"), "agree": _review("fail"),
                   "bulk": _review("pass"), "edited": _review("pass")}
        records = [
            {"pageIds": ["missed"], "decision": "request_changes", "createdAt": 20.0},
            {"pageIds": ["alarm"], "decision": "approve", "createdAt": 20.0},
            {"pageIds": ["agree"], "decision": "request_changes", "createdAt": 20.0},
            {"pageIds": ["bulk"], "decision": "approve", "createdAt": 20.0},
            {"pageIds": ["edited"], "decision": "request_changes", "createdAt": 20.0},
        ]
        # When every pass page is sampled, Then a sampled pass the teacher rejected is a miss
        metrics = agreement_metrics(pages, reviews, records, courseware_id="cw", rate=1.0)
        self.assertEqual(metrics, {"compared": 4, "agreements": 2, "missed": 1, "falseAlarms": 1})
        # When nothing is sampled, Then unsampled bulk-confirmed passes do not inflate agreement
        unsampled = agreement_metrics(pages, reviews, records, courseware_id="cw", rate=0.0)
        self.assertEqual(unsampled, {"compared": 2, "agreements": 1, "missed": 0, "falseAlarms": 1})

    def test_user_ignores_teacher_decisions_made_before_the_ai_verdict(self) -> None:
        # Given a decision recorded before the AI reviewed the page
        metrics = agreement_metrics([_page("p1")], {"p1": _review("fail", at=30.0)},
                                    [{"pageIds": ["p1"], "decision": "approve", "createdAt": 20.0}],
                                    courseware_id="cw", rate=1.0)
        # Then it is not counted as agreement or disagreement with that verdict
        self.assertEqual(metrics["compared"], 0)

    def test_user_sees_the_latest_teacher_decision_even_if_history_is_returned_out_of_order(self) -> None:
        # Given a sampled pass rejected by the teacher, then approved after a second check
        records = [
            {"pageIds": ["p1"], "decision": "approve", "createdAt": 30.0},
            {"pageIds": ["p1"], "decision": "request_changes", "createdAt": 20.0},
        ]
        # When computing the same-version sample metrics, Then chronological latest wins
        self.assertEqual(
            agreement_metrics([_page("p1")], {"p1": _review("pass")}, records, courseware_id="cw", rate=1),
            {"compared": 1, "agreements": 1, "missed": 0, "falseAlarms": 0},
        )

    def test_user_never_reviews_the_same_input_twice_but_any_content_change_counts(self) -> None:
        reviewer = {"provider": "p", "model": "m"}
        page = _page("p1")
        first = input_hash(reviewable_page(page), [{"page": 1, "text": "src"}], prompt_version="v1", reviewer=reviewer)
        # When only the workflow status changes, Then the input is the same
        decided = {**page, "reviewStatus": "approved"}
        self.assertEqual(first, input_hash(reviewable_page(decided), [{"page": 1, "text": "src"}],
                                           prompt_version="v1", reviewer=reviewer))
        # When content, source, prompt or reviewer change, Then it is a new input
        edited = {**page, "blocks": [{"blockId": "b", "kind": "text", "text": "changed"}]}
        for other in (
            input_hash(reviewable_page(edited), [{"page": 1, "text": "src"}], prompt_version="v1", reviewer=reviewer),
            input_hash(reviewable_page(page), [{"page": 1, "text": "new"}], prompt_version="v1", reviewer=reviewer),
            input_hash(reviewable_page(page), [{"page": 1, "text": "src"}], prompt_version="v2", reviewer=reviewer),
            input_hash(reviewable_page(page), [{"page": 1, "text": "src"}], prompt_version="v1",
                       reviewer={"provider": "p", "model": "other"}),
        ):
            self.assertNotEqual(first, other)

    def test_user_rejects_a_self_contradictory_model_verdict(self) -> None:
        blocking = {"code": "formula_error", "severity": "blocking", "message": "2+2=5"}
        # When the model passes a page while reporting a blocking issue, Then the output is rejected
        with self.assertRaises(ValidationError):
            AiReviewModelOutput.model_validate({"verdict": "pass", "summary": "ok", "issues": [blocking]})
        # When it fails a page without a blocking issue, Then the output is rejected too
        with self.assertRaises(ValidationError):
            AiReviewModelOutput.model_validate({"verdict": "fail", "summary": "bad", "issues": []})
        self.assertEqual(AiReviewModelOutput.model_validate(
            {"verdict": "fail", "summary": "bad", "issues": [blocking]}).verdict, "fail")
