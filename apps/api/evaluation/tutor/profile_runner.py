"""Offline profile experiment runner; no model/provider or persistence calls."""

from __future__ import annotations

import argparse
import json
from typing import Any

from domain.tutoring.learner_profile import build_learner_profile

PROFILE_CASES: tuple[dict[str, Any], ...] = (
    {
        "caseId": "profile-fresh-mastery",
        "facts": {"mastery": [{"publicationId": "pub-a", "knowledgePointId": "kp-a", "score": 0.4, "evidenceRef": "attempt-a", "observedAt": 90}]},
        "expected": {"factCount": 1, "excludedReasons": []},
    },
    {
        "caseId": "profile-stale-fact",
        "facts": {"mastery": [{"publicationId": "pub-a", "knowledgePointId": "kp-a", "score": 0.4, "evidenceRef": "attempt-old", "observedAt": 1, "expiresAt": 10}]},
        "expected": {"factCount": 0, "excludedReasons": ["stale"]},
    },
    {
        "caseId": "profile-conflict-isolated-by-publication",
        "facts": {"mastery": [
            {"publicationId": "pub-a", "knowledgePointId": "same-name-a", "score": 0.2, "evidenceRef": "attempt-a", "observedAt": 90},
            {"publicationId": "pub-b", "knowledgePointId": "same-name-b", "score": 0.9, "evidenceRef": "attempt-b", "observedAt": 90},
        ]},
        "expected": {"factCount": 2, "excludedReasons": []},
    },
)


def run_profile_case(case: dict[str, Any], *, now: float = 100) -> dict[str, Any]:
    profile = build_learner_profile(now=now, **case["facts"])
    actual = {
        "caseId": case["caseId"],
        "factCount": len(profile.facts),
        "excludedReasons": sorted(item.reason for item in profile.excluded),
        "passed": len(profile.facts) == case["expected"]["factCount"] and sorted(item.reason for item in profile.excluded) == sorted(case["expected"]["excludedReasons"]),
    }
    return actual


def check_profile_cases() -> dict[str, Any]:
    results = [run_profile_case(case) for case in PROFILE_CASES]
    return {"cases": len(results), "passed": sum(item["passed"] for item in results), "results": results, "status": "ok" if all(item["passed"] for item in results) else "failed"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not args.check:
        parser.error("当前实验只提供 --check")
    result = check_profile_cases()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
