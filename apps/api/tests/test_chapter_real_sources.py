"""User-facing workflow behavior for provenance-bound real-source review packets."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import (
    DictionaryObject,
    NameObject,
    StreamObject,
)

from evaluation.chapter.real_sources import import_reviews, prepare


def _make_pdf(path: Path, text: str) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }))
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
    })
    stream = StreamObject()
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("ascii")
    stream.set_data(b"BT /F1 12 Tf 72 720 Td (" + escaped + b") Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as output:
        writer.write(output)


class RealChapterSourceBehaviorTests(unittest.TestCase):
    def _pack(self, pdf: Path) -> dict:
        cases = []
        for index in range(10):
            cases.append({
                "id": f"math-{index}", "subject": "math", "tags": ["formula"],
                "source": {"url": "https://example.org/open.pdf", "license": "CC BY 4.0", "version": "2026", "file": str(pdf)},
                "sourcePage": 1, "sourceLocator": {"quote": "1. x = 2"},
                "expected": {"questionNumbers": ["1"], "requiredSourceFragments": ["x = 2"]},
                "input": {},
            })
        for index, tag in enumerate(("word_meaning", "reference", "explicit", "inference", "paraphrase", "wrong_evidence")):
            cases.append({
                "id": f"english-{index}", "subject": "english", "tags": [tag],
                "source": {"url": "https://example.org/open.pdf", "license": "CC BY 4.0", "version": "2026", "file": str(pdf)},
                "sourcePage": 1, "sourceLocator": {"quote": "1. x = 2"},
                "expected": {
                    "questionKind": "explicit", "answerMode": "short_answer",
                    "acceptedAnswers": ["two"], "evidenceQuote": "1. x = 2",
                },
                "input": {
                    "passage": "1. x = 2", "providedAnswer": "two",
                    "providedEvidence": "x equals two", "providedEvidenceQuote": "1. x = 2",
                },
            })
        return {"version": 1, "title": "Open chapter", "cases": cases}

    def test_user_prepares_open_chapter_then_cases_have_real_provenance_and_pending_status(self) -> None:
        # Given a local PDF and a source pack that locates every case on its cited page
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "chapter.pdf"
            _make_pdf(pdf, "1. x = 2")
            pack = self._pack(pdf)

            # When the real-source packet is prepared
            manifest = prepare(pack)

            # Then page-backed observations are retained while every case remains uncounted and pending
            math_case = manifest["cases"][0]
            self.assertEqual(math_case["sourceKind"], "real_open_material")
            self.assertEqual(math_case["actual"]["questionNumbers"], ["1"])
            self.assertEqual(math_case["actual"]["textSource"], "pypdf_text_layer")
            self.assertEqual(len(math_case["source"]["contentSha256"]), 64)
            self.assertEqual(manifest["report"]["pendingHumanReview"], 16)
            english_case = manifest["cases"][10]
            self.assertEqual(english_case["actual"]["productionEvaluation"]["assessment"], "correct")
            self.assertEqual(english_case["actual"]["productionEvaluation"]["evidenceVerdict"], "supported")
            self.assertEqual(english_case["actual"]["expectedCalibrationStatus"], "pending_human_calibration")
            self.assertIn("declaredChecks", english_case["actual"])
            self.assertFalse(manifest["report"]["counted"])
            self.assertFalse(math_case["counted"])
            self.assertIn("No OCR model", math_case["actual"]["limitations"])

    def test_user_supplies_unlocatable_source_quote_then_packet_preparation_fails_closed(self) -> None:
        # Given a validly shaped packet whose locator does not occur on the cited page
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "chapter.pdf"
            _make_pdf(pdf, "1. x = 2")
            pack = self._pack(pdf)
            pack["cases"][0]["sourceLocator"]["quote"] = "not on this page"

            # When the source is checked
            with self.assertRaisesRegex(ValueError, "not found"):
                prepare(pack)

    def test_user_imports_a_human_decision_then_only_that_accepted_case_counts(self) -> None:
        # Given a prepared packet and one explicit, source-bound human decision
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "chapter.pdf"
            _make_pdf(pdf, "1. x = 2")
            manifest = prepare(self._pack(pdf))
            case = manifest["cases"][0]
            decisions = {"decisions": [{
                "caseId": case["id"], "sourceSha256": case["source"]["contentSha256"],
                "manifestSha256": manifest["manifestSha256"],
                "caseFingerprint": case["caseFingerprint"],
                "reviewer": "reviewer@example.org", "reviewedAt": datetime.now(timezone.utc).isoformat(),
                "decision": "accepted", "rationale": "Question and condition match source.",
                "verifiedQuote": "1. x = 2",
            }]}

            # When the decision is imported
            reviewed = import_reviews(manifest, decisions)

            # Then the named case records the declaration and other pending cases remain uncounted
            self.assertEqual(reviewed["cases"][0]["reviewStatus"], "human_accepted")
            self.assertFalse(reviewed["cases"][0]["counted"])
            self.assertFalse(reviewed["cases"][1]["counted"])
            self.assertEqual(reviewed["report"]["realCaseReviewed"], 1)
            self.assertEqual(reviewed["report"]["goldCount"], 0)
            self.assertFalse(reviewed["report"]["counted"])

    def test_user_imports_stale_or_duplicate_reviews_then_import_is_rejected(self) -> None:
        # Given a fresh packet and either a forged source digest or a duplicate decision
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "chapter.pdf"
            _make_pdf(pdf, "1. x = 2")
            manifest = prepare(self._pack(pdf))
            case = manifest["cases"][0]
            decision = {
                "caseId": case["id"], "sourceSha256": "0" * 64, "reviewer": "reviewer",
                "manifestSha256": manifest["manifestSha256"],
                "caseFingerprint": case["caseFingerprint"],
                "reviewedAt": "2026-10-03T12:00:00+00:00", "decision": "accepted",
                "rationale": "checked", "verifiedQuote": "1. x = 2",
            }

            # When the import contains stale provenance or repeats an ID
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                import_reviews(copy.deepcopy(manifest), {"decisions": [decision]})
            repeated = dict(decision, sourceSha256=case["source"]["contentSha256"])
            with self.assertRaisesRegex(ValueError, "exactly once"):
                import_reviews(copy.deepcopy(manifest), {"decisions": [repeated, repeated]})

    def test_user_changes_manifest_locator_then_review_import_rejects_tampering(self) -> None:
        # Given a packet whose source locator is changed after preparation
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "chapter.pdf"
            _make_pdf(pdf, "1. x = 2")
            manifest = prepare(self._pack(pdf))
            tampered = copy.deepcopy(manifest)
            tampered["cases"][0]["sourceKind"] = "synthetic"

            # When a reviewer decision references that altered packet
            with self.assertRaisesRegex(ValueError, "manifest content hash"):
                import_reviews(tampered, {"decisions": []})

            # Recomputing the public manifest digest still cannot make the old case decision fingerprint match.
            tampered = copy.deepcopy(manifest)
            tampered["cases"][0]["expected"]["requiredSourceFragments"] = ["changed after review"]
            payload = json.dumps(
                {key: value for key, value in tampered.items() if key != "manifestSha256"},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            )
            tampered["manifestSha256"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            with self.assertRaisesRegex(ValueError, "case fingerprint"):
                import_reviews(tampered, {"decisions": []})

    def test_user_omits_english_evaluator_contract_then_prepare_returns_case_error(self) -> None:
        # Given an English case without a supported answer mode and reviewed evidence locator
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "chapter.pdf"
            _make_pdf(pdf, "1. x = 2")
            pack = self._pack(pdf)
            del pack["cases"][10]["expected"]["evidenceQuote"]

            # When the source pack is prepared
            with self.assertRaisesRegex(ValueError, "evidenceQuote"):
                prepare(pack)

    def test_user_changes_source_or_supplies_quote_from_another_page_then_import_is_rejected(self) -> None:
        # Given a prepared packet with a valid source, reviewer and packet fingerprint
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "chapter.pdf"
            _make_pdf(pdf, "1. x = 2")
            manifest = prepare(self._pack(pdf))
            case = manifest["cases"][0]
            decision = {
                "caseId": case["id"], "sourceSha256": case["source"]["contentSha256"],
                "manifestSha256": manifest["manifestSha256"], "caseFingerprint": case["caseFingerprint"],
                "reviewer": "reviewer", "reviewedAt": "2026-10-03T12:00:00+00:00",
                "decision": "accepted", "rationale": "Checked against the page.",
                "verifiedQuote": "1. x = 2",
            }

            # When either the PDF changes or the reviewer cites text absent from it
            _make_pdf(pdf, "1. x = 3")
            with self.assertRaisesRegex(ValueError, "source file changed"):
                import_reviews(copy.deepcopy(manifest), {"decisions": [decision]})
            _make_pdf(pdf, "1. x = 2")
            forged = dict(decision, verifiedQuote="invented source text")
            with self.assertRaisesRegex(ValueError, "verifiedQuote"):
                import_reviews(copy.deepcopy(manifest), {"decisions": [forged]})


if __name__ == "__main__":
    unittest.main()
