"""Image preview acceptance tests use only the external model runtime boundary."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from typing import Any

from application.services.tutor_model_evaluation import _arm_summary, _run_arm
from infrastructure.runtime.model_runtime import ModelSelection


class ImageRuntime:
    """A model boundary that describes the bytes it actually received."""

    def generate_json_as(self, provider: str, model: str, prompt: str, schema: dict[str, Any],
                         **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        from PIL import Image

        paths = kwargs.get("image_paths") or []
        with Image.open(paths[0]) as picture:
            output = {"size": list(picture.size)}
        return output, {"durationMs": 1, "usage": {"promptTokens": None, "outputTokens": None}}


class TutorImageEvaluationAcceptanceTests(unittest.TestCase):
    def test_user_receives_svg_as_png_or_an_explicit_missing_renderer_outcome(self) -> None:
        """Given an SVG diagram, When evaluating, Then send a real PNG or report the missing renderer."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "picture.svg"
            image.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 7 5"><rect width="7" height="5" fill="white"/></svg>')
            result = _run_arm(ImageRuntime(), _case(image), ModelSelection("codex", "default"), asset_root=root)  # type: ignore[arg-type]
        if result["status"] == "skipped":
            self.assertEqual(result["reason"], "image_conversion_unavailable")
            self.assertIsNone(result["output"])
        else:
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["output"]["size"], [1120, 800])
            self.assertEqual(len(result["deliveredImageSha256"]), 64)

    def test_user_evaluates_actual_image_bytes(self) -> None:
        """Given a pinned PNG, When evaluating, Then the model receives decodable image bytes."""
        from PIL import Image

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "picture.png"
            Image.new("RGB", (7, 5), "white").save(image)
            case = _case(image)
            outcome = _run_arm(ImageRuntime(), case, ModelSelection("codex", "default"), asset_root=root)  # type: ignore[arg-type]
        self.assertEqual(outcome["status"], "success")
        self.assertEqual(outcome["output"], {"size": [7, 5]})
        self.assertEqual(outcome["inputMode"], "image")
        self.assertIsNone(outcome["cost"])
        self.assertIsNone(outcome["promptTokens"])

    def test_user_keeps_missing_changed_and_unsupported_cases_in_report(self) -> None:
        """Given absent or altered assets or no vision, When evaluating, Then retain explicit outcomes."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "picture.png"
            image.write_bytes(b"not a picture")
            case = _case(image)
            unsupported = _run_arm(ImageRuntime(), case, ModelSelection("ollama", "qwen2.5:7b"), asset_root=root)  # type: ignore[arg-type]
            image.write_bytes(b"changed")
            changed = _run_arm(ImageRuntime(), case, ModelSelection("codex", "default"), asset_root=root)  # type: ignore[arg-type]
            image.unlink()
            missing = _run_arm(ImageRuntime(), case, ModelSelection("codex", "default"), asset_root=root)  # type: ignore[arg-type]
        self.assertEqual(unsupported["status"], "skipped")
        self.assertEqual(unsupported["reason"], "vision_not_declared")
        self.assertEqual(changed["status"], "failed")
        self.assertEqual(missing["status"], "failed")
        rows = [{"taskDimension": "image_understanding", "baseline": item} for item in (unsupported, changed, missing)]
        summary = _arm_summary(rows, "baseline")
        self.assertEqual(summary["cases"], 3)
        self.assertEqual(summary["skippedCalls"], 1)
        self.assertEqual(summary["failedCalls"], 2)

    def test_user_cannot_deliver_external_assets_or_svg_resources(self) -> None:
        """Given path traversal or an SVG URL, When preparing images, Then fail before calling a model."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "picture.svg"
            image.write_text('<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.com/private"/></svg>')
            case = _case(image)
            unsafe_svg = _run_arm(ImageRuntime(), case, ModelSelection("codex", "default"), asset_root=root)  # type: ignore[arg-type]
            case["input"]["imageAsset"] = "../secret.png"
            traversal = _run_arm(ImageRuntime(), case, ModelSelection("codex", "default"), asset_root=root)  # type: ignore[arg-type]
        self.assertEqual(unsafe_svg["status"], "failed")
        self.assertEqual(traversal["status"], "failed")


def _case(image: Path) -> dict[str, Any]:
    return {"caseId": "image-test", "taskDimension": "image_understanding",
            "input": {"prompt": "Read the image", "imageAsset": image.name},
            "expected": {"size": [7, 5]}, "rubric": {"must": "read the pixels"},
            "imageAssetSha256": hashlib.sha256(image.read_bytes()).hexdigest()}
