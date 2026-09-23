from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from evaluation.benchmark.contract import DEFAULT_TASK_DIMENSIONS, validate_records
from evaluation.benchmark.drafts import validate_drafts
from evaluation.benchmark.statistics import (
    paired_binary_difference,
    paired_bootstrap_ci,
    validate_arm_completeness,
)
from evaluation.prefix_cache_probe import probe_prefix_cache


def _record(case_id: str, dimension: str, *, counted: bool = True, reviewer: str = "reviewer") -> dict:
    return {
        "caseId": case_id,
        "taskDimension": dimension,
        "redactedInput": {"prompt": "脱敏合成输入"},
        "expected": {"pass": True},
        "rubric": {"factual": "must be correct"},
        "sourceKind": "fixture" if not counted else "human",
        "license": "internal-approved",
        "redaction": "已移除身份信息并由人工复核",
        "annotatorId": "annotator-a",
        "reviewerId": reviewer,
        "approvedAt": "2026-09-22T00:00:00Z",
        "strata": {
            "subject": "math" if int(case_id.split("-")[-1]) % 2 else "science",
            "gradeBand": "middle" if int(case_id.split("-")[-1]) % 3 else "high",
            "difficulty": "easy" if int(case_id.split("-")[-1]) % 2 else "hard",
        },
        "counted": counted,
    }


class BenchmarkContractTests(unittest.TestCase):
    def test_review_queue_has_50_candidates_but_no_claimed_human_approvals(self) -> None:
        review_queue = Path(__file__).resolve().parents[1] / "evaluation" / "benchmark" / "review_queue" / "candidates.jsonl"
        records = [json.loads(line) for line in review_queue.read_text(encoding="utf-8").splitlines() if line.strip()]
        result = validate_drafts(records)
        self.assertTrue(result.ok, result.problems)
        self.assertEqual(result.candidate_count, 50)
        self.assertEqual(sum(result.dimensions.values()), 50)
        self.assertEqual(validate_records(records).counted_cases, 0)
        for record in records:
            image_asset = record["input"].get("imageAsset")
            if image_asset:
                asset_path = review_queue.parent / image_asset
                self.assertTrue(asset_path.is_file(), image_asset)
                ElementTree.parse(asset_path)

    def test_review_queue_rejects_claimed_approvals_and_bad_review_state(self) -> None:
        row = {
            "caseId": "draft-1",
            "taskDimension": "error_localization",
            "input": {"prompt": "合成题"},
            "expected": {"diagnosis": "待复核"},
            "rubric": {"factual": "待复核"},
            "sourceKind": "synthetic",
            "reviewStatus": "approved",
            "counted": True,
            "license": "internal draft",
            "redaction": "synthetic",
            "strata": {"subject": "math", "gradeBand": "middle", "difficulty": "easy"},
            "annotatorId": "invented-person",
        }
        result = validate_drafts([row], minimum_candidates=1, minimum_per_dimension=0)
        self.assertFalse(result.ok)
        self.assertTrue(any("reviewStatus" in problem for problem in result.problems))
        self.assertTrue(any("counted=false" in problem for problem in result.problems))
        self.assertTrue(any("annotatorId" in problem for problem in result.problems))

    def test_malformed_json_types_fail_closed_without_traceback(self) -> None:
        malformed = _record("case-51", "error_localization")
        malformed["sourceKind"] = ["human"]
        malformed["strata"] = "bad"
        malformed["expected"] = ["pass"]
        malformed["rubric"] = "bad"
        result = validate_records([malformed, ["not", "a", "record"], None])
        self.assertFalse(result.ok)
        self.assertEqual(result.counted_cases, 0)
        self.assertTrue(any("invalid sourceKind" in item for item in result.problems))
        self.assertTrue(any("strata must be an object" in item for item in result.problems))
        self.assertTrue(any("expected must be a non-empty object" in item for item in result.problems))
        self.assertTrue(any("rubric must be a non-empty object" in item for item in result.problems))
        self.assertTrue(any("record must be a JSON object" in item for item in result.problems))

    def test_cli_returns_structured_nonzero_for_malformed_object(self) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".jsonl") as handle:
            handle.write(json.dumps({"sourceKind": ["human"], "strata": "bad"}) + "\n")
            handle.flush()
            completed = subprocess.run(
                [sys.executable, "-m", "evaluation.benchmark.validator", handle.name],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(completed.returncode, 0)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["problems"])

    def test_fixture_is_explicitly_excluded_but_full_coverage_passes(self) -> None:
        records = [
            _record(f"case-{index}", DEFAULT_TASK_DIMENSIONS[index % len(DEFAULT_TASK_DIMENSIONS)])
            for index in range(50)
        ]
        records.append(_record("fixture-1", "error_localization", counted=False))
        result = validate_records(records)
        self.assertTrue(result.ok, result.problems)
        self.assertEqual(result.counted_cases, 50)

    def test_only_human_rows_count_even_when_non_human_forgets_counted_false(self) -> None:
        records = [
            _record(f"case-{index}", DEFAULT_TASK_DIMENSIONS[index % len(DEFAULT_TASK_DIMENSIONS)])
            for index in range(49)
        ]
        records.append({**_record("public-50", "error_localization"), "sourceKind": "public", "counted": True})
        result = validate_records(records)
        self.assertFalse(result.ok)
        self.assertEqual(result.counted_cases, 49)
        self.assertTrue(any("requires at least 50" in item for item in result.problems))

    def test_count_review_identity_and_coverage_fail_closed(self) -> None:
        records = [_record("case-1", "error_localization", reviewer="annotator-a")]
        records.append(_record("case-1", "error_localization"))
        result = validate_records(records)
        self.assertFalse(result.ok)
        self.assertTrue(any("requires at least 50" in item for item in result.problems))
        self.assertTrue(any("reviewer must differ" in item for item in result.problems))
        self.assertTrue(any("duplicate caseId" in item for item in result.problems))
        self.assertTrue(any("coverage is insufficient" in item for item in result.problems))


class PairedStatisticsTests(unittest.TestCase):
    def test_bootstrap_resamples_pairs_and_is_reproducible(self) -> None:
        first = paired_bootstrap_ci([1, 2, 3, 4], [2, 3, 4, 5], resamples=500, seed=7)
        second = paired_bootstrap_ci([1, 2, 3, 4], [2, 3, 4, 5], resamples=500, seed=7)
        self.assertEqual(first, second)
        self.assertEqual(first.estimate, 1.0)
        self.assertEqual(first.sample_count, 4)

    def test_binary_difference_uses_only_discordant_pairs_for_sign_test(self) -> None:
        result = paired_binary_difference([False, True, True, False], [True, True, False, False])
        self.assertEqual(result.discordant_baseline_fail_candidate_pass, 1)
        self.assertEqual(result.discordant_baseline_pass_candidate_fail, 1)
        self.assertEqual(result.sign_p_value, 1.0)

    def test_failed_arm_completeness_requires_explicit_failures(self) -> None:
        problems = validate_arm_completeness(
            ["a", "b"],
            [{"caseId": "a", "status": "success", "passed": True}],
            arm_name="candidate",
        )
        self.assertTrue(any("missing case results" in item for item in problems))


class PrefixCacheProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = {
            "phases": {
                "cold": {"status": "success", "latencyMs": 100, "promptTokens": 30, "cacheHitTokens": 0},
                "warm": {"status": "success", "latencyMs": 90, "promptTokens": 30, "cacheHitTokens": 10},
                "control": {"status": "success", "latencyMs": 80, "promptTokens": 5, "cacheHitTokens": 0},
            }
        }

    def test_provider_token_evidence_is_implicit_support(self) -> None:
        result = probe_prefix_cache(self.fixture)
        self.assertEqual(result.capability_state, "implicit")
        self.assertEqual(result.verdict, "supported")
        self.assertTrue(result.offline)

    def test_no_token_or_official_evidence_is_inconclusive(self) -> None:
        fixture = {"phases": {phase: {**value, "cacheHitTokens": 0} for phase, value in self.fixture["phases"].items()}}
        result = probe_prefix_cache(fixture)
        self.assertEqual(result.capability_state, "unknown")
        self.assertEqual(result.verdict, "inconclusive")

    def test_official_unsupported_and_missing_phase_are_explicit(self) -> None:
        result = probe_prefix_cache(self.fixture, official_evidence={"supportsPrefixCache": False, "source": "provider-doc"})
        self.assertEqual(result.capability_state, "unsupported")
        with self.assertRaisesRegex(ValueError, "missing control"):
            probe_prefix_cache({"phases": {"cold": self.fixture["phases"]["cold"], "warm": self.fixture["phases"]["warm"]}})

    def test_only_valid_warm_hit_can_support_and_explicit_needs_source(self) -> None:
        cold_control_hits = {
            "phases": {
                "cold": {"status": "success", "latencyMs": 100, "promptTokens": 30, "cacheHitTokens": 12},
                "warm": {"status": "success", "latencyMs": 90, "promptTokens": 30, "cacheHitTokens": 0},
                "control": {"status": "success", "latencyMs": 80, "promptTokens": 5, "cacheHitTokens": 5},
            }
        }
        result = probe_prefix_cache(cold_control_hits)
        self.assertEqual(result.verdict, "inconclusive")
        invalid_warm = {"phases": {**cold_control_hits["phases"], "warm": {**cold_control_hits["phases"]["warm"], "cacheHitTokens": 31}}}
        self.assertEqual(probe_prefix_cache(invalid_warm).verdict, "inconclusive")
        self.assertEqual(
            probe_prefix_cache(self.fixture, official_evidence={"supportsPrefixCache": True}).verdict,
            "supported",
        )
        self.assertEqual(
            probe_prefix_cache(
                {"phases": {phase: {**value, "cacheHitTokens": 0} for phase, value in self.fixture["phases"].items()}},
                official_evidence={"supportsPrefixCache": True, "source": "provider-doc"},
            ).capability_state,
            "explicit",
        )

    def test_equal_hidden_cache_baseline_does_not_prove_application_prefix_reuse(self) -> None:
        fixture = {
            "phases": {
                "cold": {"status": "success", "latencyMs": 100, "promptTokens": 29020, "cacheHitTokens": 8960},
                "warm": {"status": "success", "latencyMs": 90, "promptTokens": 29020, "cacheHitTokens": 8960},
                "control": {"status": "success", "latencyMs": 95, "promptTokens": 28020, "cacheHitTokens": 8960},
            }
        }
        result = probe_prefix_cache(fixture)
        self.assertEqual(result.capability_state, "unknown")
        self.assertEqual(result.verdict, "inconclusive")


if __name__ == "__main__":
    unittest.main()
