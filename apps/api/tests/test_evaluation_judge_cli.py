"""Judge 报告契约、聚合指标和历史报告保留策略测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import evaluation.judge_cli as judge_cli
from evaluation.judge_cli import (
    JUDGE_REPORT_KIND,
    JUDGE_REPORT_VERSION,
    run_all,
    validate_report,
    write_report,
)

VALID_PAYLOAD = {
    "scores": {"clarity": 4, "targeting": 3, "factual": 5},
    "rationale": "评分依据。",
    "confidence": 0.8,
}


def _generator(*, attempts: int = 2, fallback: bool = True, fail_on: int | None = None):
    calls = 0

    def generate(provider, model, prompt, schema, max_tokens=400):
        nonlocal calls
        calls += 1
        if calls == fail_on:
            raise RuntimeError("provider unavailable")
        return VALID_PAYLOAD, {
            "durationMs": 17.5,
            "providerAttempts": attempts,
            "schemaFallback": {"used": fallback, "reason": "grammar" if fallback else None},
            "usage": {"promptTokens": 10, "outputTokens": 6},
        }

    return generate


class JudgeReportTests(unittest.TestCase):
    def test_report_contract_separates_judge_metrics_and_runtime_metadata(self) -> None:
        report = run_all("ollama", "qwen", generate_json_as=_generator())
        self.assertEqual(validate_report(report), [])
        self.assertEqual(report["reportKind"], JUDGE_REPORT_KIND)
        self.assertEqual(report["reportVersion"], JUDGE_REPORT_VERSION)
        self.assertEqual(len(report["sampleSetHash"]), 64)
        self.assertEqual(report["judge"], {
            "provider": "ollama", "model": "qwen", "promptVersion": report["judge"]["promptVersion"]
        })
        sample = report["results"][0]
        self.assertEqual(sample["logicalCalls"], 1)
        self.assertEqual(sample["providerAttempts"], 2)
        self.assertEqual(sample["tokenUsage"], {"promptTokens": 10, "outputTokens": 6})
        self.assertEqual(report["judgeMetrics"]["providerAttempts"], 6)
        self.assertEqual(report["judgeMetrics"]["p50DurationMs"], 17.5)
        self.assertNotIn("rationale", str(report))
        self.assertNotIn("modelMetrics", report)

    def test_failed_judge_is_explicit_and_checkable(self) -> None:
        report = run_all("ollama", "qwen", generate_json_as=_generator(fail_on=2))
        self.assertEqual(report["judgeMetrics"]["failureCount"], 1)
        self.assertEqual(report["results"][1]["judgeSucceeded"], False)
        self.assertEqual(validate_report(report), [])

    def test_invalid_report_contract_is_rejected(self) -> None:
        report = run_all("ollama", "qwen", generate_json_as=_generator())
        del report["results"][0]["providerAttempts"]
        problems = validate_report(report)
        self.assertIn("results[0].providerAttempts is invalid", problems)

    def test_report_writer_keeps_each_run(self) -> None:
        report = run_all("ollama", "qwen", generate_json_as=_generator())
        with tempfile.TemporaryDirectory() as directory:
            first = write_report(report, Path(directory))
            second_report = run_all("ollama", "qwen", generate_json_as=_generator())
            second = write_report(second_report, Path(directory))
            self.assertNotEqual(first, second)
            self.assertEqual(len(list(Path(directory).glob("*.json"))), 2)

    def test_check_returns_failure_for_execution_failure(self) -> None:
        report = run_all("ollama", "qwen", generate_json_as=_generator(fail_on=1))
        with tempfile.TemporaryDirectory() as directory, patch(
            "sys.argv", ["judge_cli", "--check", "--output-dir", directory]
        ), patch.object(judge_cli, "run_all", return_value=report):
            self.assertEqual(judge_cli.main(), 1)


if __name__ == "__main__":
    unittest.main()
