"""Provider protocol and activity input contracts discovered in real pilots."""
from __future__ import annotations

import unittest

from domain.contracts.courseware import (
    CoursewarePageDraftOutput,
    CoursewarePlanDraftOutput,
)
from domain.courseware.assessment import assess_activity
from domain.courseware.model_output import generation_schema
from domain.courseware.quality import document_page_issues


class CoursewareModelOutputTests(unittest.TestCase):
    def test_provider_schema_limits_generated_provenance_to_frozen_source_identifiers(self):
        schema = generation_schema(CoursewarePageDraftOutput, source={"sourceRevisionId": "frozen-source"},
            pages=[{"page": 123, "sentences": [{"sentenceId": "p123-s1"}], "regions": []}], asset_ids=["original-p123"])
        refs = schema["$defs"]["CoursewareSourceRef"]["properties"]
        self.assertEqual(refs["sourceRevisionId"]["enum"], ["frozen-source"])
        self.assertEqual(refs["page"]["enum"], [123])
        self.assertEqual(refs["sentenceIds"]["items"]["enum"], ["p123-s1"])
        self.assertEqual(refs["regionIds"]["maxItems"], 0)
        self.assertEqual(refs["assetId"]["enum"], [None, "original-p123"])
        self.assertEqual(schema["$defs"]["CoursewareImageBlock"]["properties"]["assetId"]["enum"], ["original-p123"])

    def test_closed_provider_protocol_accepts_plan_coverage_and_discriminated_page_blocks(self):
        def check(node):
            if isinstance(node, dict):
                self.assertNotIn("oneOf", node)
                if node.get("type") == "object":
                    self.assertIs(node["additionalProperties"], False)
                    self.assertEqual(set(node["required"]), set(node.get("properties", {})))
                for child in node.values():
                    check(child)
            elif isinstance(node, list):
                for child in node:
                    check(child)
        for model in (CoursewarePlanDraftOutput, CoursewarePageDraftOutput):
            check(generation_schema(model))
        self.assertEqual(generation_schema(CoursewarePlanDraftOutput)["properties"]["coverageMap"]["type"], "array")

    def test_model_coverage_decoding_preserves_evidence_and_rejects_duplicate_objectives(self):
        entries = [{"objectiveId": "reading", "sourceRefs": [{"page": 23}]}]
        self.assertEqual(CoursewarePlanDraftOutput.decode_coverage_entries(entries), {"reading": [{"page": 23}]})
        with self.assertRaises(ValueError):
            CoursewarePlanDraftOutput.decode_coverage_entries(entries + entries)

    def test_generated_schedule_uses_page_totals_without_mutating_model_output(self):
        sessions = [{"durationMinutes": 40, "sections": [{"estimatedMinutes": 99, "pages": [
            {"estimatedMinutes": 4}, {"estimatedMinutes": 6},
        ]}]}]
        fixed = CoursewarePlanDraftOutput.derive_generated_timing(sessions)
        self.assertEqual(fixed, [{"durationMinutes": 10, "sections": [{"estimatedMinutes": 10, "pages": [
            {"estimatedMinutes": 4}, {"estimatedMinutes": 6},
        ]}]}])
        self.assertEqual(sessions[0]["durationMinutes"], 40)

    def test_short_generated_outline_has_a_valid_minimum_session_without_mutating_raw_timings(self):
        raw = [{"durationMinutes": 20, "sections": [{"estimatedMinutes": 20,
                "pages": [{"estimatedMinutes": 1}, {"estimatedMinutes": 1}, {"estimatedMinutes": 1}]}]}]
        normalized = CoursewarePlanDraftOutput.derive_generated_timing(raw)
        self.assertEqual(normalized[0]["durationMinutes"], 10)
        self.assertEqual(normalized[0]["sections"][0]["estimatedMinutes"], 10)
        self.assertEqual([page["estimatedMinutes"] for page in normalized[0]["sections"][0]["pages"]], [4, 3, 3])
        self.assertEqual([page["estimatedMinutes"] for page in raw[0]["sections"][0]["pages"]], [1, 1, 1])

    def test_user_types_one_blank_then_receives_objective_feedback_while_open_answers_need_review(self):
        # Given a reviewed single blank and a teacher-reviewed open explanation
        blank = {"activityKind": "fill_blank", "assessmentMode": "objective", "answerKey": "audience"}
        # When the player submits its text field
        self.assertEqual(assess_activity(blank, {"text": " Audience "}), "correct")
        self.assertEqual(assess_activity(blank, {"text": "originator"}), "incorrect")
        # Then a free explanation is not treated as objective mastery
        self.assertEqual(assess_activity({**blank, "activityKind": "short_answer"}, {"text": "audience"}), "needs_review")


class CoursewareChoiceAssessmentTests(unittest.TestCase):
    """单选作答的两种形状必须得出同一个结论，多选仍然要求集合全等。"""

    single = {"activityKind": "choice", "assessmentMode": "objective", "answerKey": ["B"]}
    multiple = {"activityKind": "choice", "assessmentMode": "objective", "answerKey": ["A", "B"]}

    def test_user_selects_one_option_when_scalar_and_single_element_list_are_graded_alike(self) -> None:
        # Given a single-choice activity whose answer key is a one-element list
        # When the player submits a scalar selectedOption / answer or a one-element selectedOptions
        for response in (
            {"selectedOption": "B"},
            {"answer": "B"},
            {"selectedOptions": ["B"]},
            {"selectedOption": "b"},
        ):
            with self.subTest(response=response):
                # Then every valid shape is graded as correct
                self.assertEqual(assess_activity(self.single, response), "correct")

    def test_user_selects_a_wrong_option_then_every_shape_is_marked_incorrect(self) -> None:
        for response in (
            {"selectedOption": "C"},
            {"answer": "C"},
            {"selectedOptions": ["C"]},
        ):
            with self.subTest(response=response):
                self.assertEqual(assess_activity(self.single, response), "incorrect")

    def test_user_selects_multiple_options_then_the_whole_set_must_match(self) -> None:
        self.assertEqual(assess_activity(self.multiple, {"selectedOptions": ["B", "A"]}), "correct")
        self.assertEqual(assess_activity(self.multiple, {"selectedOptions": ["A"]}), "incorrect")
        # 单选标量不能当作多选的一部分蒙分。
        self.assertEqual(assess_activity(self.multiple, {"selectedOption": "A"}), "incorrect")

    def test_user_submits_no_supported_answer_shape_then_it_needs_review(self) -> None:
        self.assertEqual(assess_activity(self.single, {}), "needs_review")
        self.assertEqual(assess_activity(self.single, {"selectedOptions": []}), "needs_review")
        self.assertEqual(assess_activity(self.single, {"selectedOption": {"label": "B"}}), "needs_review")


class CoursewareExcerptQualityTests(unittest.TestCase):
    def test_ocr_heading_and_inline_tags_do_not_require_manual_excerpt_repair(self):
        pages = [{"page": 123, "text": "## 4.2 Trailblazer\n\n## Memoir Trailblazer: Ta-Nehisi Coates\nThe story should not be just <sub>y</sub>ou &amp; your life."}]
        block = {"kind": "text", "authorship": "source_excerpt", "text": "4.2 Trailblazer Memoir Trailblazer: Ta-Nehisi Coates", "sourceRefs": [{"page": 123}]}
        document = {"pages": [{"pageId": "opening", "generationStatus": "completed", "sourceRefs": [{"page": 123}], "blocks": [block]}]}
        self.assertEqual(document_page_issues(document, pages), [])
        block["text"] = "The story should not be just you & your life."
        self.assertEqual(document_page_issues(document, pages), [])
        block["text"] = "The story should be just you & your life."
        self.assertEqual([item["code"] for item in document_page_issues(document, pages)], ["excerpt_not_verbatim"])

    def test_a_paraphrase_cannot_be_approved_as_a_verbatim_textbook_excerpt(self):
        pages = [{"page": 23, "text": "Reading language rhetorically means figuring out why and how it works."}]
        block = {"kind": "text", "authorship": "source_excerpt", "text": "Ask how the message works. 中文辅助。", "sourceRefs": [{"page": 23}]}
        document = {"pages": [{"pageId": "summary", "generationStatus": "completed", "sourceRefs": [{"page": 23}], "blocks": [block]}]}
        self.assertEqual([issue["code"] for issue in document_page_issues(document, pages)], ["excerpt_not_verbatim"])
        block["authorship"] = "ai_supplement"
        self.assertEqual(document_page_issues(document, pages), [])

    def test_pdf_line_breaks_do_not_block_a_verbatim_excerpt(self):
        pages = [{"page": 23, "text": "The Digital World: Building on What\nYou Already Know"}]
        document = {"pages": [{"pageId": "cover", "generationStatus": "completed", "sourceRefs": [{"page": 23}], "blocks": [{"kind": "text", "authorship": "source_excerpt", "text": "The Digital World: Building on What You Already Know", "sourceRefs": [{"page": 23}]}]}]}
        self.assertEqual(document_page_issues(document, pages), [])
