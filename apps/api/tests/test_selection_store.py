from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infrastructure.runtime import selection_store
from infrastructure.runtime.model_runtime import ModelRuntime
from infrastructure.runtime.ocr_runtime import OcrRuntime


class SelectionStoreTests(unittest.TestCase):
    def test_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store_path = str(Path(directory) / "selection.json")
            with patch.dict(os.environ, {"DOTTY_RUNTIME_SELECTION_FILE": store_path}, clear=False):
                self.assertIsNone(selection_store.load("generation"))
                selection_store.save("generation", {"provider": "codex", "model": "gpt-5.6-luna"})
                self.assertEqual(
                    selection_store.load("generation"),
                    {"provider": "codex", "model": "gpt-5.6-luna"},
                )

    def test_unrelated_key_is_untouched_by_a_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store_path = str(Path(directory) / "selection.json")
            with patch.dict(os.environ, {"DOTTY_RUNTIME_SELECTION_FILE": store_path}, clear=False):
                selection_store.save("generation", {"provider": "codex", "model": "gpt-5.6-luna"})
                selection_store.save("review", {"provider": "codex", "model": "gpt-5.6-sol"})
                self.assertEqual(
                    selection_store.load("generation"),
                    {"provider": "codex", "model": "gpt-5.6-luna"},
                )

    def test_model_runtime_prefers_stored_selection_over_env_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store_path = str(Path(directory) / "selection.json")
            with patch.dict(os.environ, {
                "DOTTY_RUNTIME_SELECTION_FILE": store_path,
                "MODEL_PROVIDER": "codex",
                "MODEL_NAME": "default",
            }, clear=True):
                selection_store.save("generation", {"provider": "codex", "model": "gpt-5.6-luna"})
                runtime = ModelRuntime()
                self.assertEqual(runtime.selection.model, "gpt-5.6-luna")

    def test_ocr_runtime_prefers_stored_selection_over_env_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store_path = str(Path(directory) / "selection.json")
            with patch.dict(os.environ, {
                "DOTTY_RUNTIME_SELECTION_FILE": store_path,
                "OCR_PROVIDER": "mineru",
            }, clear=True):
                selection_store.save("ocr", {"provider": "pypdf"})
                runtime = OcrRuntime()
                self.assertEqual(runtime.selection.provider, "pypdf")


if __name__ == "__main__":
    unittest.main()
