"""Business acceptance for OCR fidelity and independent human admission."""
from __future__ import annotations

import copy
import unittest

from application.services.courseware import CoursewareService
from domain.chapters.source import pages_from_ocr
from domain.courseware.profiles import CoursewareProfileUnavailable, courseware_profiles
from evaluation.benchmark.admission import admit_case, snapshot_hash
from evaluation.benchmark.contract import validate_records
from ocr_quality import evaluate_page_quality
from prompts import CATALOG


class QualityExpansionTests(unittest.TestCase):
    def test_user_selects_science_then_receives_explicit_scientific_source_constraints(self) -> None:
        # Given a registered third subject, When selecting its prompts, Then it is not a math fallback.
        profile = courseware_profiles.get("science")
        prompt = CATALOG[profile.page_prompt_id]
        rendered = prompt.render(chapter="Test", section="Test", page_outline="Test", objectives="Test",
                                 audience="Test", language_policy="Chinese", source="SOURCE")
        self.assertIn("distinguish an observation", rendered)
        self.assertIn("teacher supervision", rendered)
        self.assertNotEqual(profile.page_prompt_id, courseware_profiles.get("math").page_prompt_id)
        with self.assertRaises(CoursewareProfileUnavailable):
            courseware_profiles.get("unconfigured-subject")

    def test_user_sees_ocr_badcase_without_a_guessed_replacement(self) -> None:
        """Given damaged OCR, When assessing it, Then preserve text and quarantine."""
        text = r"$$ - 1 3 \frac { 7 } { 3 } = - 1 3 6 $$"
        result = evaluate_page_quality(text, page_number=214, provider="mineru", retry_count=2)
        self.assertEqual(result["status"], "quarantine")
        self.assertEqual(result["formulaReview"]["originalText"], text)
        self.assertEqual(result["formulaReview"]["status"], "requires_human_review")
        self.assertNotIn("correctedText", result["formulaReview"])
        pages = pages_from_ocr("<!-- page 214 -->\n" + text)
        self.assertEqual(pages[0]["text"], text)
        with self.assertRaisesRegex(ValueError, "来源未就绪"):
            CoursewareService._selected_pages({"pageStart": 214, "pageEnd": 214, "pages": pages})

    def test_user_admits_only_two_independent_verified_humans(self) -> None:
        """Given individual identity evidence, When both approve the same snapshot, Then admit."""
        row = _candidate()
        digest = snapshot_hash(row)
        reviews = [{"actorId": actor, "snapshotSha256": digest, "decision": "approve",
                    "reviewedAt": "2026-10-12T00:00:00Z", "rationale": "核对来源与标注"}
                   for actor in ("a", "b")]
        admitted = admit_case(row, reviews, _identities())
        validation = validate_records([admitted], trusted_identities=_identities(),
                                      min_counted_cases=1, min_cases_per_dimension=0)
        self.assertEqual(validation.counted_cases, 1)
        changed = copy.deepcopy(row)
        changed["expected"] = {"pass": False}
        with self.assertRaisesRegex(ValueError, "snapshot"):
            admit_case(changed, reviews, _identities())
        shared = _identities()
        shared["b"]["personKey"] = "person-a"
        with self.assertRaisesRegex(ValueError, "independent"):
            admit_case(row, reviews, shared)

    def test_user_cannot_count_self_declared_identity_or_public_material_as_gold(self) -> None:
        """Given shared/demo or public sources, When claiming gold, Then coverage remains zero."""
        row = _candidate()
        row.update(annotatorId="a", reviewerId="b", approvedAt="2026-10-12T00:00:00Z", counted=True)
        self.assertEqual(validate_records([row]).counted_cases, 0)
        row["sourceKind"] = "public"
        with self.assertRaisesRegex(ValueError, "human"):
            admit_case(row, [], _identities())


def _identities() -> dict:
    return {actor: {"identityVerified": True, "actorKind": "individual", "personKey": "person-" + actor,
                    "evidenceRef": "offline-identity-check-" + actor} for actor in ("a", "b")}


def _candidate() -> dict:
    return {"caseId": "human-test", "taskDimension": "error_localization", "sourceKind": "human",
            "source": {"contentSha256": "a" * 64, "reference": "deidentified-material-1"},
            "redactedInput": {"prompt": "脱敏练习"}, "expected": {"pass": True},
            "rubric": {"accuracy": "原页核对"}, "license": "internal-consent",
            "redaction": "已移除身份信息", "strata": {"subject": "math", "gradeBand": "middle", "difficulty": "easy"}}
