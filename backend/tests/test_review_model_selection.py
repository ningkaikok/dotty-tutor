from __future__ import annotations

import unittest
from unittest.mock import patch

from infrastructure.runtime.model_runtime import runtime
from infrastructure.runtime.review_runtime import ReviewRuntime


class ReviewModelSelectionTests(unittest.TestCase):
    def test_text_reviewer_changes_without_touching_generation_selection(self) -> None:
        reviewer = ReviewRuntime()
        generation_before = (runtime.selection.provider, runtime.selection.model)
        providers = [{
            "id": "codex",
            "label": "Codex 订阅",
            "available": True,
            "models": ["default", "gpt-5.6-sol"],
            "detail": "test",
        }]
        with patch.object(runtime, "providers", return_value=providers):
            catalog = reviewer.select_text("codex", "gpt-5.6-sol")

        self.assertEqual(catalog["selected"], {
            "provider": "codex",
            "model": "gpt-5.6-sol",
        })
        self.assertEqual(
            (runtime.selection.provider, runtime.selection.model),
            generation_before,
        )

    def test_rejects_unavailable_review_provider(self) -> None:
        reviewer = ReviewRuntime()
        providers = [{
            "id": "ollama",
            "label": "Ollama",
            "available": False,
            "models": [],
            "detail": "offline",
        }]
        with patch.object(runtime, "providers", return_value=providers):
            with self.assertRaisesRegex(ValueError, "当前不可用于审核"):
                reviewer.select_text("ollama", "qwen2.5:7b")

    def test_catalog_uses_one_reviewer_for_text_and_images(self) -> None:
        reviewer = ReviewRuntime()
        self.assertEqual(reviewer.text_provider, "codex")
        self.assertEqual(reviewer.text_model, "gpt-5.6-sol")
        self.assertNotIn("visionSelected", reviewer.catalog())


if __name__ == "__main__":
    unittest.main()
