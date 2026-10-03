"""用户验收场景：英语章节答案与来源证据独立判定。"""

from __future__ import annotations

import unittest

from domain.chapters.english import evaluate_english_chapter_attempt

REVISION = "revision-english-1"
REQUIRED_REF = {
    "sourceRevisionId": REVISION,
    "page": 3,
    "regionId": "paragraph-1",
    "sentenceId": "sentence-1",
}
SOURCES = [
    {
        "sourceRevisionId": REVISION,
        "page": 3,
        "text": "Maya left the red umbrella by the door. The notice said the train leaves at nine.",
        "regions": [{"regionId": "paragraph-1", "x": 0.1, "y": 0.2, "width": 0.8, "height": 0.3}],
        "sentences": [
            {"sentenceId": "sentence-1", "regionId": "paragraph-1", "text": "Maya left the red umbrella by the door."},
            {"sentenceId": "sentence-2", "regionId": "paragraph-1", "text": "The notice said the train leaves at nine."},
        ],
    }
]


def evaluate(
    *,
    question_kind: str = "explicit",
    answer_mode: str = "objective",
    accepted: list[str] | None = None,
    required_refs: list[dict] | None = None,
    answer: dict | None = None,
    evidence_refs: list[dict] | None = None,
    source_pages: list[dict] | None = None,
    rubric: dict | None = None,
) -> dict:
    question = {
        "questionKind": question_kind,
        "answerMode": answer_mode,
        "acceptedAnswers": accepted if accepted is not None else ["Maya left it by the door."],
        "requiredEvidenceRefs": required_refs if required_refs is not None else [REQUIRED_REF],
        "sourceRevisionId": REVISION,
        "rubric": rubric or {},
    }
    attempt = answer if answer is not None else {"text": "Maya left it by the door."}
    refs = evidence_refs if evidence_refs is not None else [{**REQUIRED_REF, "quote": "Maya left the red umbrella by the door."}]
    pages = source_pages if source_pages is not None else SOURCES
    return evaluate_english_chapter_attempt(
        question=question,
        answer=attempt,
        evidence_refs=refs,
        source_pages=pages,
    )


class EnglishChapterEvaluationBehaviorTests(unittest.TestCase):
    def test_user_answers_word_meaning_reference_and_explicit_items_then_each_uses_its_reviewed_choices(self) -> None:
        # Given the three objective English question kinds with approved variants
        # When each answer cites its pinned source sentence
        outcomes = [evaluate(question_kind=kind)["assessment"] for kind in ("word_meaning", "reference", "explicit")]

        # Then all three use the same deterministic objective boundary
        self.assertEqual(outcomes, ["correct", "correct", "correct"])

    def test_user_gives_an_approved_objective_answer_with_matching_source_then_it_is_correct(self) -> None:
        # Given an objective English question with an approved answer and pinned source sentence
        result = evaluate()

        # When its answer and source reference are evaluated
        # Then both answer and source binding must be supported
        self.assertEqual(result["assessment"], "correct")
        self.assertEqual(result["evidenceVerdict"], "supported")

    def test_user_gives_a_wrong_objective_answer_with_valid_source_then_it_is_incorrect(self) -> None:
        # Given a valid source reference and an answer outside the declared objective choices
        result = evaluate(answer={"text": "Lena borrowed a blue coat."})

        # When deterministic objective grading runs
        # Then the answer is incorrect while the cited location remains supported
        self.assertEqual(result["assessment"], "incorrect")
        self.assertEqual(result["evidenceVerdict"], "supported")

    def test_user_gives_the_right_answer_with_the_wrong_source_then_it_needs_review(self) -> None:
        # Given a correct answer but a citation to a different sentence on the same page
        result = evaluate(evidence_refs=[{
            "sourceRevisionId": REVISION,
            "page": 3,
            "regionId": "paragraph-1",
            "sentenceId": "sentence-2",
            "quote": "The notice said the train leaves at nine.",
        }])

        # When answer and source evidence are assessed independently
        # Then correct wording cannot hide the mismatched source attribution
        self.assertEqual(result["assessment"], "needs_review")
        self.assertEqual(result["evidenceVerdict"], "mismatch")

    def test_user_omits_required_source_evidence_then_correct_answer_needs_review(self) -> None:
        # Given the required source citation is absent
        result = evaluate(evidence_refs=[])

        # When the otherwise correct answer is assessed
        # Then the evaluator records missing evidence and withholds correctness
        self.assertEqual(result["assessment"], "needs_review")
        self.assertEqual(result["evidenceVerdict"], "missing")

    def test_user_writes_an_approved_short_answer_variant_then_it_is_accepted(self) -> None:
        # Given the teacher has explicitly reviewed two equivalent answer forms
        result = evaluate(
            question_kind="short_answer",
            answer_mode="short_answer",
            accepted=["Maya left it by the door.", "She placed the umbrella beside the entrance."],
            answer={"value": "She placed the umbrella beside the entrance."},
        )

        # When the learner uses the second reviewed form with matching evidence
        # Then the explicit approved variant is accepted
        self.assertEqual(result["assessment"], "correct")
        self.assertEqual(result["evidenceVerdict"], "supported")

    def test_user_uses_an_unlisted_reasonable_paraphrase_then_it_needs_review(self) -> None:
        # Given an open response whose wording is absent from the approved variants
        result = evaluate(
            question_kind="short_answer",
            answer_mode="short_answer",
            accepted=["Maya left it by the door."],
            answer={"text": "She put the umbrella next to the entrance."},
        )

        # When the deterministic approved-variant check cannot match it
        # Then the evaluator abstains for human review instead of calling it incorrect
        self.assertEqual(result["assessment"], "needs_review")
        self.assertEqual(result["evidenceVerdict"], "supported")

    def test_user_asks_for_unsupported_inference_then_the_evaluator_abstains(self) -> None:
        # Given the source has not been approved to support the requested inference
        result = evaluate(
            question_kind="inference",
            answer={"text": "Maya was upset."},
            accepted=["Maya was upset."],
            rubric={"supportStatus": "unsupported"},
        )

        # When an otherwise matching answer and citation are submitted
        # Then unsupported inference remains for review
        self.assertEqual(result["assessment"], "needs_review")
        self.assertEqual(result["evidenceVerdict"], "supported")

    def test_user_answers_supported_inference_then_reviewed_answer_and_source_can_pass(self) -> None:
        # Given an inference that a reviewer marked as supported by its source sentence
        result = evaluate(
            question_kind="inference",
            answer={"text": "Maya was upset."},
            accepted=["Maya was upset."],
            rubric={"supportStatus": "supported"},
        )

        # When the approved answer and required citation both match
        # Then deterministic objective evaluation may accept the item
        self.assertEqual(result["assessment"], "correct")
        self.assertEqual(result["evidenceVerdict"], "supported")

    def test_user_cites_a_region_outside_the_source_page_then_evidence_mismatches(self) -> None:
        # Given the correct answer and page but a region that is absent from that page
        result = evaluate(evidence_refs=[{
            **REQUIRED_REF,
            "regionId": "unlisted-region",
            "quote": "Maya left the red umbrella by the door.",
        }])

        # When the reference is resolved against the source revision
        # Then an unowned region cannot support the answer
        self.assertEqual(result["assessment"], "needs_review")
        self.assertEqual(result["evidenceVerdict"], "mismatch")

    def test_user_selects_every_objective_option_then_ambiguous_answer_needs_review(self) -> None:
        # Given conflicting text and multiple selected options
        result = evaluate(answer={
            "text": "Maya left it by the door.",
            "selectedOptions": ["Maya left it by the door.", "Lena borrowed the blue coat."],
        })

        # When the objective response is normalized
        # Then the evaluator refuses to treat any matching fragment as a single choice
        self.assertEqual(result["assessment"], "needs_review")

    def test_user_selects_multiple_objective_options_without_text_then_it_needs_review(self) -> None:
        # Given an objective response that selects all options at once
        result = evaluate(answer={"selectedOptions": ["Maya left it by the door.", "Lena borrowed the blue coat."]})

        # When the evaluator checks a single-answer objective contract
        # Then it abstains on the ambiguous selection set
        self.assertEqual(result["assessment"], "needs_review")

    def test_user_cites_a_sentence_id_missing_from_source_index_then_evidence_mismatches(self) -> None:
        # Given a syntactically valid locator whose sentence ID is absent from the indexed page
        result = evaluate(
            required_refs=[{**REQUIRED_REF, "sentenceId": "not-indexed"}],
            evidence_refs=[{**REQUIRED_REF, "sentenceId": "not-indexed", "quote": "Maya left the red umbrella by the door."}],
        )

        # When the evaluator resolves the reference against page and sentence manifests
        # Then an invented sentence ID cannot establish evidence
        self.assertEqual(result["assessment"], "needs_review")
        self.assertEqual(result["evidenceVerdict"], "mismatch")

    def test_user_adds_an_invalid_extra_reference_then_valid_reference_cannot_mask_it(self) -> None:
        # Given one valid citation plus an invalid extra value
        result = evaluate(evidence_refs=[
            {**REQUIRED_REF, "quote": "Maya left the red umbrella by the door."},
            "not-a-reference",
        ])

        # When the whole evidence set is checked
        # Then the malformed entry makes the set invalid instead of being filtered away
        self.assertEqual(result["assessment"], "needs_review")
        self.assertEqual(result["evidenceVerdict"], "mismatch")

    def test_user_supplies_a_quote_from_another_sentence_under_the_required_id_then_it_mismatches(self) -> None:
        # Given the correct sentence ID paired with text copied from another sentence
        result = evaluate(evidence_refs=[{
            **REQUIRED_REF,
            "quote": "The notice said the train leaves at nine.",
        }])

        # When quote text is checked against the selected sentence
        # Then the source key alone cannot authorize unrelated quoted text
        self.assertEqual(result["assessment"], "needs_review")
        self.assertEqual(result["evidenceVerdict"], "mismatch")

    def test_user_cites_an_old_revision_then_the_location_is_rejected(self) -> None:
        # Given a citation that points to an earlier source revision
        result = evaluate(evidence_refs=[{
            **REQUIRED_REF,
            "sourceRevisionId": "revision-old",
            "quote": "Maya left the red umbrella by the door.",
        }])

        # When the attempt is checked against the current page set
        # Then stale evidence cannot authorize the answer
        self.assertEqual(result["assessment"], "needs_review")
        self.assertEqual(result["evidenceVerdict"], "mismatch")

    def test_user_receives_feedback_then_private_answers_are_not_returned(self) -> None:
        # Given a question containing an approved answer
        result = evaluate()

        # When the evaluator returns evidence for persistence
        # Then it records a verdict without copying answer keys or learner text
        self.assertIn("feedback", result)
        serialized = str(result["evaluationEvidence"])
        self.assertNotIn("Maya left it by the door", serialized)
        self.assertNotIn("acceptedAnswers", serialized)

    def test_user_sends_invalid_question_discriminators_then_evaluator_fails_closed(self) -> None:
        # Given malformed non-string question and answer discriminators
        result = evaluate_english_chapter_attempt(
            question={"questionKind": [], "answerMode": [], "acceptedAnswers": ["answer"]},
            answer={"text": "answer"},
            evidence_refs=[{**REQUIRED_REF, "quote": "Maya left the red umbrella by the door."}],
            source_pages=SOURCES,
        )

        # When the public evaluator validates untrusted contract values
        # Then it returns needs_review without raising a membership type error
        self.assertEqual(result["assessment"], "needs_review")
        self.assertEqual(result["evaluationEvidence"]["questionKind"], "unknown")


if __name__ == "__main__":
    unittest.main()
