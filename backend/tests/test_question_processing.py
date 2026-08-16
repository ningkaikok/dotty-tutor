"""Tests for bounded automatic recovery in the question pipeline."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from application.services.question_processing import _generate_validated_question


def _candidate() -> tuple[dict, list[dict], dict]:
    payload = {
        "question": {
            "id": "question-1",
            "questionType": "short-answer",
            "prompt": "计算 1+1。",
            "options": [],
            "givens": [],
        },
        "lessonSteps": [],
        "architecture": {},
        "modelRun": {"provider": "test", "model": "test", "fallback": False},
    }
    return payload, [], payload["modelRun"]


class QuestionQualityRecoveryTests(unittest.TestCase):
    def test_retries_only_the_failed_question_until_quality_is_ready(self) -> None:
        quality_attempts = 0

        def apply_gate(payload: dict, _source: str, _images: list[str]) -> dict:
            nonlocal quality_attempts
            quality_attempts += 1
            status = "ready" if quality_attempts == 2 else "needs_review"
            quality = {
                "status": status,
                "errors": [] if status == "ready" else ["题型结构不完整"],
                "warnings": [],
                "validatorVersion": "test-v1",
            }
            payload["quality"] = quality
            payload["question"]["publicationStatus"] = status
            return quality

        with TemporaryDirectory() as directory:
            with (
                patch(
                    "application.services.question_processing.generate_lesson",
                    side_effect=lambda _source, **_kwargs: _candidate(),
                ) as generate,
                patch(
                    "application.services.question_processing.review_lesson_payload",
                    side_effect=lambda payload, _source, _images, _cards: (
                        payload,
                        {"provider": "test"},
                    ),
                ),
                patch("application.services.question_processing.apply_question_quality_gate", side_effect=apply_gate),
            ):
                payload, _cards, _model_run, _review_run = _generate_validated_question(
                    number="2",
                    block="2. 计算 1+1。",
                    images=[],
                    index=0,
                    batch={"id": "batch-1", "startPage": 1, "endPage": 1},
                    ocr_run={},
                    asset_dir=Path(directory),
                )

        self.assertEqual(generate.call_count, 2)
        self.assertIsNone(generate.call_args_list[0].kwargs["repair_errors"])
        self.assertEqual(generate.call_args_list[1].kwargs["repair_errors"], ["题型结构不完整"])
        self.assertEqual(payload["quality"]["status"], "ready")
        self.assertEqual(payload["qualityRecovery"], {
            "attempts": 2,
            "recovered": True,
            "quarantined": False,
        })

    def test_runtime_fallback_is_quarantined_without_repeating_the_outage(self) -> None:
        fallback = _candidate()
        fallback[0]["modelRun"] = {
            "provider": "mock",
            "model": "fallback",
            "fallback": True,
        }
        fallback = fallback[0], fallback[1], fallback[0]["modelRun"]

        def failed_gate(payload: dict, _source: str, _images: list[str]) -> dict:
            quality = {
                "status": "needs_review",
                "errors": ["题型结构不完整"],
                "warnings": [],
                "validatorVersion": "test-v1",
            }
            payload["quality"] = quality
            return quality

        with TemporaryDirectory() as directory:
            with (
                patch("application.services.question_processing.generate_lesson", return_value=fallback) as generate,
                patch(
                    "application.services.question_processing.review_lesson_payload",
                    side_effect=lambda payload, _source, _images, _cards: (
                        payload,
                        {"provider": "test"},
                    ),
                ),
                patch("application.services.question_processing.apply_question_quality_gate", side_effect=failed_gate),
            ):
                payload, _cards, _model_run, _review_run = _generate_validated_question(
                    number="3",
                    block="3. 计算 2+2。",
                    images=[],
                    index=0,
                    batch={"id": "batch-1", "startPage": 1, "endPage": 1},
                    ocr_run={},
                    asset_dir=Path(directory),
                )

        self.assertEqual(generate.call_count, 1)
        self.assertEqual(payload["qualityRecovery"], {
            "attempts": 1,
            "recovered": False,
            "quarantined": True,
        })


if __name__ == "__main__":
    unittest.main()
