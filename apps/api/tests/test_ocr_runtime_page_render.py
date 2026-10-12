"""Acceptance that Poppler previews long source PDFs at the requested page."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter

from infrastructure.runtime.ocr_runtime import OcrRuntime


@unittest.skipUnless(shutil.which("pdftoppm"), "requires Poppler's pdftoppm")
class OcrRuntimePageRenderTests(unittest.TestCase):
    def test_user_can_preview_a_page_after_pdf_page_numbers_are_zero_padded(self) -> None:
        # Given an original PDF with more than 999 pages, where Poppler pads its
        # default output suffix using the total page count
        with tempfile.TemporaryDirectory(prefix="dotty-long-pdf-") as temporary:
            root = Path(temporary)
            source = root / "long-source.pdf"
            writer = PdfWriter()
            for _ in range(1000):
                writer.add_blank_page(width=240, height=320)
            with source.open("wb") as output:
                writer.write(output)

            # When a teacher requests page 1000
            result = OcrRuntime().render_page_image(source, 1000, root / "rendered", "")

            # Then the adapter returns one predictable, real PNG for that page
            self.assertIsNotNone(result)
            self.assertEqual(result[0], "images/rendered-page-1000.png")
            raster = root / "rendered" / "rendered-page-1000.png"
            self.assertTrue(raster.is_file())
            with raster.open("rb") as rendered:
                self.assertEqual(rendered.read(8), b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
