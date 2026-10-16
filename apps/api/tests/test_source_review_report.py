"""Offline OCR/source review reports preserve observations without inventing approval."""
from __future__ import annotations

import copy
import unittest

from evaluation.chapter.source_review_report import build_source_review_report


def _candidate():
    return {"schemaVersion": "teaching-candidate-v1", "inputKind": "paper_scan", "humanReview": "pending",
            "document": {"contentSha256": "a" * 64, "file": "source.pdf"},
            "chapterRequest": {"subject": "science", "title": "细胞", "source": {
                "sourceVersion": "fixture-v1", "sourceUrl": "https://example.org/cells.pdf",
                "license": "CC0-1.0", "attributionText": "原创测试", "distributionScope": "private",
                "pages": [{"page": 12, "text": "Cellsarestructuralunits. x^=4", "flags": ["unreadable"]}]}}}


class SourceReviewReportAcceptanceTests(unittest.TestCase):
    def test_user_compares_transcription_without_overwriting_raw_ocr_or_approving_science(self) -> None:
        """Given raw OCR and a proposed transcript, When comparing, Then preserve raw text and show pending differences."""
        candidate = _candidate()
        before = copy.deepcopy(candidate)
        report = build_source_review_report(candidate, {"12": "Cells are structural units. x^2=4"})
        self.assertEqual(candidate, before)
        page = report["pages"][0]
        self.assertEqual(page["observedText"], candidate["chapterRequest"]["source"]["pages"][0]["text"])
        self.assertEqual(page["proposedText"], "Cells are structural units. x^2=4")
        self.assertTrue(page["differences"])
        self.assertEqual(page["sourceLocator"], {"sourceFileSha256": "a" * 64, "page": 12})
        self.assertEqual(report["humanReview"], "pending")
        self.assertIsNone(report["ocrAccuracy"])
        self.assertFalse(report["teachingArtifactReady"])
        self.assertIn("核对科学概念、单位及图表", report["reviewChecklist"])

    def test_user_without_transcription_receives_explicit_missing_review(self) -> None:
        """Given no manual transcript, When preparing the report, Then do not claim a measured OCR result."""
        report = build_source_review_report(_candidate(), {})
        self.assertEqual(report["pages"][0]["status"], "awaiting_transcription")
        self.assertIsNone(report["pages"][0]["proposedText"])
        self.assertIsNone(report["ocrAccuracy"])

    def test_user_cannot_compare_a_foreign_page_or_count_a_candidate_as_human_gold(self) -> None:
        """Given foreign page text or invalid source metadata, When comparing, Then reject the packet."""
        with self.assertRaises(ValueError):
            build_source_review_report(_candidate(), {"13": "foreign page"})
        candidate = _candidate()
        candidate["document"]["contentSha256"] = "missing"
        with self.assertRaises(ValueError):
            build_source_review_report(candidate, {})
