from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from infrastructure.runtime.model_runtime import runtime
from infrastructure.runtime.review_runtime import ReviewRuntime


class ReviewModelSelectionTests(unittest.TestCase):
    def test_reviewer_protects_and_restores_image_references(self) -> None:
        reviewer = ReviewRuntime()
        text_review = {
            "verdict": "pass",
            "correctedPrompt": "主视图 ⟦IMG_1⟧，俯视图 ⟦IMG_2⟧",
            "correctedGivens": [],
            "correctedOptions": [],
            "correctedLessonSteps": [
                {"title": "步骤", "text": "继续推理", "speechText": "继续推理"}
                for _ in range(4)
            ],
            "corrections": [],
            "issues": [],
            "confidence": 100,
            "needsHumanReview": False,
        }
        vision_review = {
            "correctAnswer": "",
            "imageAssessments": [],
            "issues": [],
            "confidence": 100,
            "needsHumanReview": False,
        }
        payload = {
            "question": {"prompt": "主视图，俯视图", "imageUrls": ["/api/uploads/front.jpg", "/api/uploads/top.jpg"]},
            "lessonSteps": [{"title": "步骤", "text": "继续推理", "speechText": "继续推理"} for _ in range(4)],
        }
        source = "7. 主视图 ![](images/front.jpg)，俯视图 ![](images/top.jpg)"

        with patch.object(
            runtime,
            "generate_json_as",
            side_effect=[
                (text_review, {"provider": "codex", "model": "default", "fallback": False}),
                (vision_review, {"provider": "codex", "model": "default", "fallback": False}),
            ],
        ) as generate:
            reviewed, review_run = reviewer.review(payload, source, [Path("front.jpg"), Path("top.jpg")])

        first_prompt = generate.call_args_list[0].args[2]
        self.assertNotIn("images/front.jpg", first_prompt)
        self.assertIn("⟦IMG_1⟧", first_prompt)
        self.assertEqual(
            reviewed["question"]["prompt"],
            "主视图 ![](images/front.jpg)，俯视图 ![](images/top.jpg)",
        )
        self.assertEqual(review_run["imagePlaceholderAudit"]["status"], "ready")

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
