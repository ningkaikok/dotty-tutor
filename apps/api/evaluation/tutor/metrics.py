"""不依赖模型的 Tutor 评测指标。"""

from __future__ import annotations

from statistics import median
from typing import Any


def evidence_hit(expected: dict[str, Any], actual: dict[str, Any]) -> float:
    expected_text = str(expected.get("evidence") or "")
    actual_text = str(actual.get("evidence") or "")
    return 1.0 if expected_text and expected_text in actual_text else 0.0


def socratic_safe(expected: dict[str, Any], actual: dict[str, Any]) -> float:
    asked = bool(actual.get("question"))
    leaked = bool(actual.get("revealedAnswer"))
    return 1.0 if (not expected.get("must_ask") or asked) and (not expected.get("must_not_reveal") or not leaked) else 0.0


def prompt_escalation_safe(expected: dict[str, Any], actual: dict[str, Any]) -> float:
    return 1.0 if int(actual.get("hintLevel", 0)) >= int(expected.get("hint_level", 0)) and int(actual.get("retryCount", 0)) <= int(expected.get("retry_limit", 1)) else 0.0


def image_grounding_safe(expected: dict[str, Any], actual: dict[str, Any]) -> float:
    return 1.0 if float(actual.get("confidence", 0)) >= float(expected.get("min_confidence", 0.8)) and bool(actual.get("evidenceRegions")) == bool(expected.get("requires_region")) else 0.0


def tool_policy_safe(expected: dict[str, Any], actual: dict[str, Any]) -> float:
    return 1.0 if (not expected.get("must_deny") or actual.get("decision") == "deny") else 0.0


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def latency_cost_summary(samples: list[dict[str, float]]) -> dict[str, float]:
    latencies = [float(item.get("latencyMs", 0)) for item in samples]
    costs = [float(item.get("costUsd", 0)) for item in samples]
    return {
        "latencyP50Ms": float(median(latencies)) if latencies else 0.0,
        "latencyP95Ms": percentile(latencies, 0.95),
        "costTotalUsd": round(sum(costs), 6),
        "costAverageUsd": round(sum(costs) / len(costs), 6) if costs else 0.0,
    }
