"""Teacher candidate preparation preserves evidence and fails closed."""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evaluation.chapter.material_candidates import prepare_candidate
from tests.test_chapter_real_sources import _make_pdf


class MaterialCandidateBehaviorTests(unittest.TestCase):
    def _args(self) -> dict:
        return {"subject": "science", "title": "Cells", "source_url": "https://example.org/cells.pdf",
                "source_version": "2026-10", "license_name": "CC BY-SA 3.0", "attribution": "Wikibooks contributors",
                "page_start": 1, "page_end": 1}

    def test_user_prepares_pdf_with_joined_words_then_raw_text_is_flagged_without_approval(self) -> None:
        # Given a PDF with source words concatenated by its text layer
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "cells.pdf"
            text = "Cellsarestructuralunitsthatmakeupplantsandanimals"
            _make_pdf(pdf, text)
            # When a teaching candidate is prepared
            candidate = prepare_candidate(pdf, **self._args())
            # Then the original observation and fingerprint remain, with no invented review
            page = candidate["chapterRequest"]["source"]["pages"][0]
            self.assertEqual(page["text"].strip(), text)
            self.assertEqual(page["flags"], ["unreadable"])
            self.assertIn("joined_words_suspected", candidate["diagnostics"][0]["warnings"])
            self.assertEqual(candidate["document"]["contentSha256"], hashlib.sha256(pdf.read_bytes()).hexdigest())
            self.assertEqual(candidate["humanReview"], "pending")
            self.assertFalse(candidate["counted"])
            self.assertFalse(candidate["modelExecuted"])

    def test_user_supplies_scan_without_text_then_report_does_not_claim_ocr_accuracy(self) -> None:
        # Given a declared scan with an empty text layer
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "scan.pdf"
            _make_pdf(pdf, "")
            # When the source is prepared without invoking an OCR provider
            candidate = prepare_candidate(pdf, input_kind="paper_scan", **self._args())
            # Then absence of text is visible and scan quality stays unverified
            self.assertEqual(candidate["diagnostics"][0]["warnings"], ["empty_text_layer"])
            self.assertFalse(candidate["ocrExecuted"])
            self.assertEqual(candidate["chapterRequest"]["source"]["distributionScope"], "private")

    def test_user_supplies_out_of_range_or_credential_url_then_candidate_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "cells.pdf"
            _make_pdf(pdf, "Cells contain membranes.")
            for overrides in ({"page_end": 2}, {"source_url": "https://user:secret@example.org/x"}, {"license_name": ""}):
                with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                    prepare_candidate(pdf, **{**self._args(), **overrides})

    def test_user_targets_original_pdf_as_output_then_cli_does_not_overwrite_source(self) -> None:
        # Given a source PDF and a mistaken output pointing to that same file
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "cells.pdf"
            _make_pdf(pdf, "Cells contain membranes.")
            original = pdf.read_bytes()
            args = self._args()
            command = [sys.executable, "-m", "evaluation.chapter.material_candidates", str(pdf), str(pdf)]
            for key, value in args.items():
                flag = "--license" if key == "license_name" else "--" + key.replace("_", "-")
                command.extend((flag, str(value)))
            # When preparation attempts exclusive output creation
            result = subprocess.run(command, capture_output=True, text=True)
            # Then the CLI rejects replacement and leaves source bytes unchanged
            self.assertEqual(result.returncode, 2)
            self.assertEqual(pdf.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
