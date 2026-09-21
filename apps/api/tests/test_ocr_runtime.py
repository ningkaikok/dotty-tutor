from __future__ import annotations

import io
import json
import os
import unittest
import zipfile
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

    def test_cloud_api_parses_markdown_and_preserves_assets(self) -> None:
        class FakeResponse:
            def __init__(self, body: bytes) -> None:
                self.body = body

            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return self.body

        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as result_zip:
            result_zip.writestr("full.md", "# 题目\n\n![图](image.png)\n")
            result_zip.writestr("image.png", b"png-bytes")
            result_zip.writestr("full_content_list.json", '{"pages": []}')
            result_zip.writestr("layout.json", '{"layout": []}')
        archive_bytes = archive.getvalue()

        def fake_urlopen(request: object, timeout: int) -> FakeResponse:
            url = getattr(request, "full_url", "")
            if url.endswith("/api/v4/file-urls/batch"):
                return FakeResponse(
                    json.dumps(
                        {
                            "code": 0,
                            "data": {
                                "batch_id": "batch-1",
                                "file_urls": ["https://upload.example/source.pdf"],
                            },
                        }
                    ).encode()
                )
            if url.endswith("/api/v4/extract-results/batch/batch-1"):
                return FakeResponse(
                    json.dumps(
                        {
                            "code": 0,
                            "data": {
                                "extract_result": [
                                    {"state": "done", "full_zip_url": "https://cdn.example/result.zip"}
                                ]
                            },
                        }
                    ).encode()
                )
            if url == "https://cdn.example/result.zip":
                return FakeResponse(archive_bytes)
            if url == "https://upload.example/source.pdf":
                return FakeResponse(b"")
            raise AssertionError(f"unexpected URL: {url}")

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            source.write_bytes(b"pdf-bytes")
            assets = root / "assets"
            with patch.dict(
                os.environ,
                {
                    "MINERU_API_KEY": "test-token-not-written-to-output",
                    "MINERU_API_POLL_INTERVAL_SECONDS": "0.5",
                },
                clear=False,
            ), patch.object(OcrRuntime, "mineru_command", return_value=None), patch(
                "infrastructure.runtime.ocr_runtime.urllib.request.urlopen",
                side_effect=fake_urlopen,
            ):
                runtime = OcrRuntime()
                markdown, run = runtime.parse(source, 0, 1, assets, "/uploads")
                self.assertIn("# 题目", markdown)
                self.assertEqual(run["mode"], "precision-api")
                self.assertEqual(run["startPage"], 1)
                self.assertEqual(run["endPage"], 2)
                self.assertEqual((assets / "image.png").read_bytes(), b"png-bytes")
                self.assertEqual((assets / "source.content_list.json").read_text(), '{"pages": []}')
                self.assertEqual((assets / "source.middle.json").read_text(), '{"layout": []}')
                self.assertNotIn("test-token-not-written-to-output", json.dumps(run))


if __name__ == "__main__":
    unittest.main()
