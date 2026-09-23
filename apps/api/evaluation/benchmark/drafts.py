"""Validation for synthetic benchmark candidates awaiting human review.

Drafts are kept outside the counted gold set. This validator makes it easy to
check review coverage without accepting generated examples as human evidence.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from evaluation.benchmark.contract import (
    DEFAULT_TASK_DIMENSIONS,
    REQUIRED_STRATA,
    _has_sensitive_key,
    load_jsonl,
)

PENDING_REVIEW = "pending_human_review"
REVIEW_METADATA = ("annotatorId", "reviewerId", "approvedAt")


@dataclass(frozen=True)
class DraftValidation:
    """Summary of a review packet, separate from official gold-set counts."""

    problems: tuple[str, ...]
    candidate_count: int
    dimensions: dict[str, int]
    strata: dict[str, int]

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "problems": list(self.problems),
            "candidateCount": self.candidate_count,
            "countedHumanCases": 0,
            "dimensions": dict(self.dimensions),
            "strataCoverage": dict(self.strata),
        }


def validate_drafts(
    records: Iterable[Any],
    *,
    minimum_candidates: int = 50,
    minimum_per_dimension: int = 5,
) -> DraftValidation:
    """Fail closed unless all rows are clearly synthetic and pending human review."""
    rows = list(records)
    problems: list[str] = []
    ids: list[str] = []
    dimensions: dict[str, int] = {dimension: 0 for dimension in DEFAULT_TASK_DIMENSIONS}
    strata_values: dict[str, set[str]] = {field: set() for field in REQUIRED_STRATA}

    for index, row in enumerate(rows, start=1):
        prefix = f"record {index}"
        if not isinstance(row, Mapping):
            problems.append(f"{prefix}: record must be an object")
            continue

        case_id = row.get("caseId")
        if not isinstance(case_id, str) or not case_id.strip():
            problems.append(f"{prefix}: caseId is required")
        else:
            ids.append(case_id)

        dimension = row.get("taskDimension")
        if not isinstance(dimension, str) or dimension not in dimensions:
            problems.append(f"{prefix} {case_id!r}: unsupported taskDimension")
        else:
            dimensions[dimension] += 1

        for field in ("input", "expected", "rubric"):
            if not isinstance(row.get(field), Mapping) or not row[field]:
                problems.append(f"{prefix} {case_id!r}: {field} must be a non-empty object")
        if isinstance(row.get("input"), Mapping):
            sensitive_key = _has_sensitive_key(row["input"])
            if sensitive_key:
                problems.append(f"{prefix} {case_id!r}: input contains sensitive key {sensitive_key!r}")
        if row.get("sourceKind") != "synthetic":
            problems.append(f"{prefix} {case_id!r}: review drafts must remain sourceKind=synthetic")
        if row.get("reviewStatus") != PENDING_REVIEW:
            problems.append(f"{prefix} {case_id!r}: reviewStatus must be {PENDING_REVIEW}")
        if row.get("counted") is not False:
            problems.append(f"{prefix} {case_id!r}: review drafts must set counted=false")
        for field in REVIEW_METADATA:
            if field in row:
                problems.append(f"{prefix} {case_id!r}: remove {field} until a human review is recorded")
        if not isinstance(row.get("strata"), Mapping):
            problems.append(f"{prefix} {case_id!r}: strata must include subject, gradeBand and difficulty")
        else:
            for field in REQUIRED_STRATA:
                value = row["strata"].get(field)
                if not isinstance(value, str) or not value.strip():
                    problems.append(f"{prefix} {case_id!r}: strata.{field} is required")
                else:
                    strata_values[field].add(value)
        if not isinstance(row.get("license"), str) or not row["license"].strip():
            problems.append(f"{prefix} {case_id!r}: license is required")
        if not isinstance(row.get("redaction"), str) or not row["redaction"].strip():
            problems.append(f"{prefix} {case_id!r}: redaction explanation is required")

    for case_id in sorted({case_id for case_id in ids if ids.count(case_id) > 1}):
        problems.append(f"duplicate caseId: {case_id}")
    if len(rows) < minimum_candidates:
        problems.append(f"candidate count is {len(rows)}, requires at least {minimum_candidates}")
    for dimension, count in dimensions.items():
        if count < minimum_per_dimension:
            problems.append(f"candidate coverage is insufficient for {dimension}: {count}, requires {minimum_per_dimension}")
    strata_coverage = {field: len(values) for field, values in strata_values.items()}
    for field, count in strata_coverage.items():
        if count < 2:
            problems.append(f"candidate strata coverage is insufficient for {field}: need at least 2 values")

    return DraftValidation(tuple(problems), len(rows), dimensions, strata_coverage)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate synthetic benchmark candidates awaiting human review.")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        result = validate_drafts(load_jsonl(args.path))
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "problems": [str(error)]}, ensure_ascii=False))
        return 2
    print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
