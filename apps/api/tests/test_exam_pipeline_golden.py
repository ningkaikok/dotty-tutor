from __future__ import annotations

import unittest

from evaluation.exam_pipeline_evaluator import (
    evaluate_exam_ir_fixture,
    evaluate_staged_pipeline_fixture,
)
from evaluation.golden_fixtures import (
    EXAM_IR_FIXTURE,
    STAGED_PIPELINE_FIXTURE,
    VERIFIER_BLOCKED_FIXTURE,
)


class ExamPipelineGoldenTests(unittest.TestCase):
    def test_exam_ir_fixture(self) -> None:
        self.assertTrue(evaluate_exam_ir_fixture(EXAM_IR_FIXTURE)["passed"])

    def test_staged_pipeline_fixture(self) -> None:
        self.assertTrue(evaluate_staged_pipeline_fixture(STAGED_PIPELINE_FIXTURE)["passed"])

    def test_verifier_blocked_fixture_skips_tutor_model(self) -> None:
        result = evaluate_staged_pipeline_fixture(VERIFIER_BLOCKED_FIXTURE)
        self.assertTrue(result["passed"])
