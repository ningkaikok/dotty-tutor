"""Offline prefix-cache capability probe contract.

The probe consumes recorded fixtures only.  It never discovers providers,
sends prompts, or treats a latency difference as proof of caching.  A positive
result requires either provider-reported cache-hit tokens or an explicit
capability statement supplied by an operator from official provider evidence.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

CAPABILITY_STATES = {"unknown", "unsupported", "implicit", "explicit"}
PHASES = ("warm", "cold", "control")


@dataclass(frozen=True)
class ProbePhaseResult:
    phase: str
    status: str
    latency_ms: float | None
    prompt_tokens: int | None
    cache_hit_tokens: int | None
    provider_evidence: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "status": self.status,
            "latencyMs": self.latency_ms,
            "promptTokens": self.prompt_tokens,
            "cacheHitTokens": self.cache_hit_tokens,
            "providerEvidence": self.provider_evidence,
            "error": self.error,
        }


@dataclass(frozen=True)
class PrefixCacheProbeResult:
    provider: str
    capability_state: str
    verdict: str
    offline: bool
    phases: dict[str, ProbePhaseResult]
    evidence: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "capabilityState": self.capability_state,
            "verdict": self.verdict,
            "offline": self.offline,
            "phases": {key: value.as_dict() for key, value in self.phases.items()},
            "evidence": list(self.evidence),
        }


def _phase(value: Mapping[str, Any], expected_phase: str) -> ProbePhaseResult:
    status = value.get("status")
    if status not in {"success", "failed"}:
        raise ValueError(f"{expected_phase}: status must be success or failed")
    latency = value.get("latencyMs")
    cache_hit = value.get("cacheHitTokens")
    prompt_tokens = value.get("promptTokens")
    if latency is not None and (not isinstance(latency, (int, float)) or latency < 0):
        raise ValueError(f"{expected_phase}: latencyMs must be a non-negative number")
    for field, number in (("promptTokens", prompt_tokens), ("cacheHitTokens", cache_hit)):
        if number is not None and (not isinstance(number, int) or number < 0):
            raise ValueError(f"{expected_phase}: {field} must be a non-negative integer")
    if status == "failed" and not str(value.get("error") or "").strip():
        raise ValueError(f"{expected_phase}: failed result requires error")
    return ProbePhaseResult(
        expected_phase,
        status,
        float(latency) if latency is not None else None,
        prompt_tokens,
        cache_hit,
        str(value.get("providerEvidence")) if value.get("providerEvidence") else None,
        str(value.get("error")) if value.get("error") else None,
    )


def classify_capability(phases: Iterable[ProbePhaseResult], official_evidence: Mapping[str, Any] | None = None) -> tuple[str, str, tuple[str, ...]]:
    """Classify capability without inferring support from timing alone."""
    observations = tuple(phases)
    by_phase = {item.phase: item for item in observations}
    warm = by_phase.get("warm")
    # Only cache-hit tokens attributable to the repeated application prefix
    # can demonstrate reuse. Providers may report a constant cached baseline
    # for their own hidden instructions in cold, warm and control requests;
    # counting that baseline as an application-prefix hit is a false positive.
    warm_cache_hit = warm.cache_hit_tokens if warm and warm.status == "success" else None
    warm_prompt_tokens = warm.prompt_tokens if warm and warm.status == "success" else None
    baseline_hits = max(
        (
            item.cache_hit_tokens or 0
            for item in observations
            if item.phase in {"cold", "control"} and item.status == "success"
        ),
        default=0,
    )
    valid_warm_hit = (
        isinstance(warm_cache_hit, int)
        and warm_cache_hit > baseline_hits
        and isinstance(warm_prompt_tokens, int)
        and warm_cache_hit <= warm_prompt_tokens
    )
    official = official_evidence or {}
    explicitly_supported = official.get("supportsPrefixCache") is True
    explicitly_unsupported = official.get("supportsPrefixCache") is False
    evidence: list[str] = []
    if valid_warm_hit:
        evidence.append("provider_reported_incremental_warm_cache_hit_tokens")
    source = official.get("source")
    if source:
        evidence.append(f"official:{source}")
    if explicitly_unsupported:
        return "unsupported", "unsupported", tuple(evidence)
    # An explicit claim is actionable only when it can be traced to an
    # operator-supplied official source.  A bare boolean is not evidence.
    if explicitly_supported and isinstance(source, str) and source.strip():
        return "explicit", "supported", tuple(evidence)
    if valid_warm_hit:
        return "implicit", "supported", tuple(evidence)
    return "unknown", "inconclusive", tuple(evidence)


def probe_prefix_cache(
    fixture: Mapping[str, Any],
    *,
    provider: str = "offline-fixture",
    official_evidence: Mapping[str, Any] | None = None,
) -> PrefixCacheProbeResult:
    """Validate and classify a previously recorded warm/cold/control fixture."""
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError("provider is required")
    raw_phases = fixture.get("phases")
    if not isinstance(raw_phases, Mapping):
        raise ValueError("fixture.phases must be an object")
    phases: dict[str, ProbePhaseResult] = {}
    for phase in PHASES:
        raw = raw_phases.get(phase)
        if not isinstance(raw, Mapping):
            raise ValueError(f"fixture is missing {phase} phase")
        phases[phase] = _phase(raw, phase)
    state, verdict, evidence = classify_capability(phases.values(), official_evidence)
    return PrefixCacheProbeResult(provider.strip(), state, verdict, True, phases, evidence)


DEFAULT_OFFLINE_FIXTURE: dict[str, Any] = {
    "phases": {
        "cold": {"status": "success", "latencyMs": 100, "promptTokens": 100, "cacheHitTokens": 0},
        "warm": {"status": "success", "latencyMs": 98, "promptTokens": 100, "cacheHitTokens": 0},
        "control": {"status": "success", "latencyMs": 99, "promptTokens": 10, "cacheHitTokens": 0},
    }
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check an offline prefix-cache fixture; never calls a provider.")
    parser.add_argument("fixture", nargs="?", type=str, help="JSON fixture path; omitted uses the built-in control fixture")
    args = parser.parse_args()
    try:
        fixture = DEFAULT_OFFLINE_FIXTURE if args.fixture is None else json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        result = probe_prefix_cache(fixture)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "offline": True, "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
