from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from infrastructure.runtime.ocr_runtime import OcrRuntime


class OcrRuntimeTests(unittest.TestCase):
    def test_mineru_is_the_default_requested_provider(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OCR_PROVIDER", None)
            self.assertEqual(OcrRuntime().selection.provider, "mineru")

    def test_default_mineru_reports_pypdf_when_command_is_unavailable(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OCR_PROVIDER", None)
            with patch.object(OcrRuntime, "mineru_command", return_value=None):
                catalog = OcrRuntime().catalog()
        self.assertEqual(catalog["selected"], "mineru")
        self.assertEqual(catalog["effective"], "pypdf")

    def test_honours_an_explicit_executable(self) -> None:
        with TemporaryDirectory() as directory:
            command = Path(directory) / "mineru"
            command.write_text("#!/bin/sh\n", encoding="utf-8")
            command.chmod(command.stat().st_mode | 0o111)
            with patch.dict(os.environ, {"MINERU_COMMAND": str(command)}, clear=False):
                self.assertEqual(OcrRuntime().mineru_command(), command)

    def test_missing_mineru_explains_docker_boundary(self) -> None:
        with patch.object(OcrRuntime, "mineru_command", return_value=None):
            catalog = OcrRuntime().catalog()
        mineru = next(item for item in catalog["providers"] if item["id"] == "mineru")
        self.assertFalse(mineru["available"])
        expected_hint = "Docker" if Path("/.dockerenv").is_file() else "未安装"
        self.assertIn(expected_hint, mineru["detail"])


if __name__ == "__main__":
    unittest.main()
