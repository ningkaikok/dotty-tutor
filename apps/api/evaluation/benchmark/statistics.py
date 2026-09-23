"""Pure paired statistics for offline model-arm comparisons."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class BootstrapInterval:
    estimate: float
    lower: float
    upper: float
    sample_count: int
    resamples: int
    confidence: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "estimate": self.estimate,
            "lower": self.lower,
            "upper": self.upper,
            "sampleCount": self.sample_count,
            "resamples": self.resamples,
            "confidence": self.confidence,
        }


def paired_bootstrap_ci(
    baseline: Sequence[float],
    candidate: Sequence[float],
    *,
    confidence: float = 0.95,
    resamples: int = 10_000,
    seed: int = 0,
) -> BootstrapInterval:
    """Bootstrap mean(candidate - baseline), resampling complete pairs."""
    if len(baseline) != len(candidate) or not baseline:
        raise ValueError("paired bootstrap requires equally sized non-empty arms")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1")
    if resamples < 1:
        raise ValueError("resamples must be positive")
    differences = [float(right) - float(left) for left, right in zip(baseline, candidate)]
    estimate = sum(differences) / len(differences)
    rng = random.Random(seed)
    samples = [
        sum(differences[rng.randrange(len(differences))] for _ in differences) / len(differences)
        for _ in range(resamples)
    ]
    samples.sort()
    alpha = (1 - confidence) / 2
    lower = samples[min(len(samples) - 1, max(0, math.floor(alpha * len(samples))))]
    upper = samples[min(len(samples) - 1, max(0, math.ceil((1 - alpha) * len(samples)) - 1))]
    return BootstrapInterval(estimate, lower, upper, len(differences), resamples, confidence)


@dataclass(frozen=True)
class PairedBinaryDifference:
    baseline_successes: int
    candidate_successes: int
    sample_count: int
    discordant_baseline_fail_candidate_pass: int
    discordant_baseline_pass_candidate_fail: int
    difference: float
    sign_p_value: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "baselineSuccesses": self.baseline_successes,
            "candidateSuccesses": self.candidate_successes,
            "sampleCount": self.sample_count,
            "baselineFailCandidatePass": self.discordant_baseline_fail_candidate_pass,
            "baselinePassCandidateFail": self.discordant_baseline_pass_candidate_fail,
            "difference": self.difference,
            "signPValue": self.sign_p_value,
        }


def paired_binary_difference(baseline: Sequence[bool], candidate: Sequence[bool]) -> PairedBinaryDifference:
    """Return paired pass-rate difference and exact two-sided sign-test p-value."""
    if len(baseline) != len(candidate) or not baseline:
        raise ValueError("paired binary comparison requires equally sized non-empty arms")
    if any(not isinstance(value, bool) for value in (*baseline, *candidate)):
        raise ValueError("binary outcomes must be bool")
    b_to_c = sum(not old and new for old, new in zip(baseline, candidate))
    c_to_b = sum(old and not new for old, new in zip(baseline, candidate))
    discordant = b_to_c + c_to_b
    tail = min(b_to_c, c_to_b)
    p_value = 1.0 if discordant == 0 else min(1.0, 2 * sum(math.comb(discordant, k) for k in range(tail + 1)) / (2 ** discordant))
    return PairedBinaryDifference(
        sum(baseline), sum(candidate), len(baseline), b_to_c, c_to_b,
        (sum(candidate) - sum(baseline)) / len(baseline), p_value,
    )


def validate_arm_completeness(expected_case_ids: Sequence[str], arm_results: Sequence[dict[str, object]], *, arm_name: str = "arm") -> list[str]:
    """Fail closed when an arm drops failures or reports duplicate/unknown cases."""
    expected = list(expected_case_ids)
    expected_set = set(expected)
    actual_ids = [str(row.get("caseId") or "") for row in arm_results]
    problems: list[str] = []
    if len(actual_ids) != len(set(actual_ids)):
        problems.append(f"{arm_name}: duplicate caseId in arm results")
    missing = sorted(expected_set - set(actual_ids))
    unknown = sorted(set(actual_ids) - expected_set)
    if missing:
        problems.append(f"{arm_name}: missing case results: {missing}")
    if unknown:
        problems.append(f"{arm_name}: unknown case results: {unknown}")
    for row in arm_results:
        case_id = row.get("caseId")
        status = row.get("status")
        if status not in {"success", "failed"}:
            problems.append(f"{arm_name} {case_id!r}: status must be success or failed")
        if status == "failed" and not str(row.get("error") or "").strip():
            problems.append(f"{arm_name} {case_id!r}: failed result must include error")
        if status == "success" and "passed" not in row:
            problems.append(f"{arm_name} {case_id!r}: success result must include passed")
    if len(actual_ids) != len(expected):
        problems.append(f"{arm_name}: result count {len(actual_ids)} does not equal expected {len(expected)}")
    return problems
