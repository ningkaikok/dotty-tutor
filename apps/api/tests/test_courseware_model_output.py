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

    def test_user_types_one_blank_then_receives_objective_feedback_while_open_answers_need_review(self):
        # Given a reviewed single blank and a teacher-reviewed open explanation
        blank = {"activityKind": "fill_blank", "assessmentMode": "objective", "answerKey": "audience"}
        # When the player submits its text field
        self.assertEqual(assess_activity(blank, {"text": " Audience "}), "correct")
        self.assertEqual(assess_activity(blank, {"text": "originator"}), "incorrect")
        # Then a free explanation is not treated as objective mastery
        self.assertEqual(assess_activity({**blank, "activityKind": "short_answer"}, {"text": "audience"}), "needs_review")


class CoursewareExcerptQualityTests(unittest.TestCase):
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
