"""Badcase 登记簿与对比工具的完整性测试。

登记簿是坏样本生命周期的唯一事实来源，这里固化它的不变量：
标签必须注册、fixed 必须有证据、语料特征化条目与 open 状态一一对应。
"""

from __future__ import annotations

import unittest

from evaluation import corpus as corpus_module
from evaluation.badcase import (
    cross_check_with_corpus,
    load_badcases,
    transition_status,
    validate_registry,
)
from evaluation.compare import compare_reports
from evaluation.labels import validate_tags


def _sample_report(entry_overrides: dict[str, dict]) -> dict:
    entries = []
    for entry_id, overrides in entry_overrides.items():
        entry = {"id": entry_id, "passed": True, "checks": [], "documenting_bug": None}
        entry.update(overrides)
        entries.append(entry)
    return {"corpusVersion": "1", "entries": entries}


class LabelTaxonomyTests(unittest.TestCase):
    def test_corpus_tags_are_registered(self) -> None:
        for entry in corpus_module.CORPUS:
            unknown = validate_tags(entry.get("tags", []), entry["id"])
            self.assertEqual(
                unknown, [], f"{entry['id']} uses unregistered tags: {unknown}"
            )


class BadcaseRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = load_badcases()

    def test_registry_is_consistent(self) -> None:
        self.assertEqual(validate_registry(self.data), [])

    def test_registry_cross_references_with_corpus(self) -> None:
        problems = cross_check_with_corpus(self.data, corpus_module.CORPUS)
        self.assertEqual(problems, [])

    def test_fixed_requires_resolution_evidence(self) -> None:
        with self.assertRaises(ValueError):
            transition_status(self.data, "question-number-subproblem-a", "fixed")
        with self.assertRaises(ValueError):
            transition_status(
                self.data, "question-number-subproblem-a", "fixed",
                resolution_note="改了正则", fixed_release="",
            )

    def test_illegal_transitions_are_rejected(self) -> None:
        # fixed → wontfix 不合法：已修复的问题要放弃必须先重新打开。
        with self.assertRaises(ValueError):
            transition_status(
                self.data, "question-number-line-break-loss", "wontfix"
            )

    def test_unknown_badcase_raises(self) -> None:
        with self.assertRaises(KeyError):
            transition_status(self.data, "no-such-case", "fixed",
                              resolution_note="x", fixed_release="1")


class CompareReportsTests(unittest.TestCase):
    def test_detects_regression_fix_and_bug_flip(self) -> None:
        old = _sample_report({
            "stable-entry": {},
            "broken-entry": {"passed": False},
            "bug-entry": {"passed": True, "documenting_bug": "T0/x"},
        })
        new = _sample_report({
            "stable-entry": {"passed": False},
            "broken-entry": {"passed": True},
            "bug-entry": {"passed": False, "documenting_bug": "T0/x"},
            "brand-new-entry": {},
        })
        comparison = compare_reports(old, new)
        self.assertEqual(comparison["regressions"], ["stable-entry"])
        self.assertEqual(comparison["fixes"], ["broken-entry"])
        self.assertEqual(comparison["bugSignatureChanged"], ["bug-entry"])
        self.assertEqual(comparison["added"], ["brand-new-entry"])
        self.assertEqual(comparison["removed"], [])

    def test_no_changes_reports_clean(self) -> None:
        report = _sample_report({"a": {}, "b": {"passed": False}})
        comparison = compare_reports(report, report)
        self.assertFalse(any(comparison[key] for key in
                             ("regressions", "fixes", "bugSignatureChanged")))

    def test_compares_model_scores_latency_and_calls(self) -> None:
        base = {
            "reportKind": "judge",
            "corpusVersion": "explanation-samples-v1",
            "sampleSetHash": "same",
            "judge": {"provider": "ollama", "model": "qwen", "promptVersion": "v1"},
            "judgeMetrics": {
                "successRate": 0.5, "failureCount": 1, "p50DurationMs": 120.0,
                "p95DurationMs": 160.0, "logicalCalls": 3, "providerAttempts": 3,
                "schemaFallbackCount": 0,
            },
            "results": [{"id": "sample", "judgeSucceeded": True,
                         "scores": {"clarity": 3.0, "targeting": 3.0, "factual": 4.0}}],
        }
        updated = {
            **base,
            "judgeMetrics": {
                "successRate": 1.0, "failureCount": 0, "p50DurationMs": 135.5,
                "p95DurationMs": 170.0, "logicalCalls": 4, "providerAttempts": 4,
                "schemaFallbackCount": 0,
            },
            "results": [{"id": "sample", "judgeSucceeded": True,
                         "scores": {"clarity": 3.5, "targeting": 3.0, "factual": 4.0}}],
        }
        comparison = compare_reports(base, updated)
        self.assertEqual(comparison["judge"]["metrics"]["logicalCalls"]["delta"], 1.0)
        self.assertEqual(comparison["judge"]["metrics"]["failureCount"]["delta"], -1.0)
        self.assertEqual(comparison["judge"]["scoreComparison"]["delta"]["clarity"], 0.5)

    def test_judge_scores_are_not_comparable_when_configuration_changes(self) -> None:
        base = {
            "reportKind": "judge", "corpusVersion": "v1", "sampleSetHash": "same",
            "judge": {"provider": "ollama", "model": "qwen-a", "promptVersion": "p1"},
            "judgeMetrics": {"logicalCalls": 1},
            "results": [{"id": "sample", "judgeSucceeded": True,
                         "scores": {"clarity": 3, "targeting": 3, "factual": 3}}],
        }
        updated = {**base, "judge": {**base["judge"], "model": "qwen-b"}}
        comparison = compare_reports(base, updated)
        self.assertFalse(comparison["judge"]["comparable"])
        self.assertIsNone(comparison["judge"]["scoreComparison"])
        self.assertIn("judge.model differs", comparison["judge"]["incomparableReasons"])

    def test_missing_metric_is_unavailable_not_zero(self) -> None:
        base = {
            "reportKind": "judge", "corpusVersion": "v1", "sampleSetHash": "same",
            "judge": {"provider": "ollama", "model": "qwen", "promptVersion": "p1"},
            "judgeMetrics": {"logicalCalls": 1}, "results": [],
        }
        updated = {**base, "judgeMetrics": {"logicalCalls": 2}}
        comparison = compare_reports(base, updated)
        self.assertIsNone(comparison["judge"]["metrics"]["failureCount"]["delta"])
        self.assertIn("failureCount", comparison["judge"]["unavailableMetrics"])


if __name__ == "__main__":
    unittest.main()
