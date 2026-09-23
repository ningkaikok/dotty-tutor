"""Human gold-standard JSONL contract and fail-closed validator.

Each counted row is a reviewed, de-identified case.  ``counted: false`` is
reserved for small development fixtures and is deliberately excluded from the
50-case and coverage gates.  The validator reports every problem so a dataset
owner can repair one review pass instead of discovering failures serially.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

DEFAULT_TASK_DIMENSIONS = (
    "error_localization",
    "socratic_question",
    "prompt_escalation",
    "image_understanding",
    "tool_overreach",
    "latency_cost",
)
MIN_COUNTED_CASES = 50
MIN_CASES_PER_DIMENSION = 5
REQUIRED_STRATA = ("subject", "gradeBand", "difficulty")
ALLOWED_SOURCE_KINDS = {"human", "synthetic", "public", "fixture"}
SENSITIVE_KEYS = {
    "studentid", "learnerid", "studentname", "learnername", "email", "phone",
    "phonenumber", "address", "schoolid", "classid", "userid",
}


@dataclass(frozen=True)
class BenchmarkValidation:
    """Machine-readable validation result suitable for a CI command."""

    problems: tuple[str, ...]
    counted_cases: int
    dimensions: dict[str, int]
    strata: dict[str, int]

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "problems": list(self.problems),
            "countedCases": self.counted_cases,
            "dimensions": dict(self.dimensions),
            "strata": dict(self.strata),
        }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load non-empty JSONL records with line-numbered parse failures."""
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"line {line_number}: invalid JSON: {error.msg}") from error
        if not isinstance(value, dict):
            raise ValueError(f"line {line_number}: record must be a JSON object")
        records.append(value)
    return records


def _has_sensitive_key(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = "".join(character for character in str(key).lower() if character.isalnum())
            if normalized in SENSITIVE_KEYS:
                return str(key)
            found = _has_sensitive_key(nested)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _has_sensitive_key(nested)
            if found:
                return found
    return None


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _non_empty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_records(
    records: Iterable[Any],
    *,
    required_dimensions: Iterable[str] = DEFAULT_TASK_DIMENSIONS,
    min_counted_cases: int = MIN_COUNTED_CASES,
    min_cases_per_dimension: int = MIN_CASES_PER_DIMENSION,
) -> BenchmarkValidation:
    """Validate schema, review independence, count and coverage gates."""
    rows = list(records)
    problems: list[str] = []
    required = tuple(required_dimensions)
    ids: list[str] = []
    counted: list[Mapping[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        prefix = f"record {index}"
        if not isinstance(row, Mapping):
            problems.append(f"{prefix}: record must be a JSON object")
            continue
        case_id = row.get("caseId")
        if not _non_empty_text(case_id):
            problems.append(f"{prefix}: caseId is required")
        else:
            ids.append(str(case_id))
        dimension = row.get("taskDimension")
        if dimension not in required:
            problems.append(f"{prefix} {case_id!r}: taskDimension must be one of {required}")
        redacted_input = row.get("redactedInput", row.get("input"))
        if not isinstance(redacted_input, Mapping) or not redacted_input:
            problems.append(f"{prefix} {case_id!r}: redacted input must be a non-empty object")
        if not isinstance(row.get("expected"), Mapping) or not row["expected"]:
            problems.append(f"{prefix} {case_id!r}: expected must be a non-empty object")
        rubric = row.get("rubric")
        if not isinstance(rubric, Mapping) or not rubric:
            problems.append(f"{prefix} {case_id!r}: rubric must be a non-empty object")
        source_kind = row.get("sourceKind")
        source_kind_valid = isinstance(source_kind, str) and source_kind in ALLOWED_SOURCE_KINDS
        if not source_kind_valid:
            problems.append(f"{prefix} {case_id!r}: invalid sourceKind")
        if not _non_empty_text(row.get("license")):
            problems.append(f"{prefix} {case_id!r}: license is required")
        if not _non_empty_text(row.get("redaction")):
            problems.append(f"{prefix} {case_id!r}: redaction explanation is required")
        if not _non_empty_text(row.get("annotatorId")):
            problems.append(f"{prefix} {case_id!r}: annotatorId is required")
        if not _non_empty_text(row.get("reviewerId")):
            problems.append(f"{prefix} {case_id!r}: reviewerId is required")
        elif row.get("reviewerId") == row.get("annotatorId"):
            problems.append(f"{prefix} {case_id!r}: annotator and reviewer must differ")
        if not _valid_timestamp(row.get("approvedAt")):
            problems.append(f"{prefix} {case_id!r}: approvedAt must be an ISO-8601 timestamp")
        strata = row.get("strata")
        if not isinstance(strata, Mapping):
            problems.append(f"{prefix} {case_id!r}: strata must be an object")
        else:
            for key in REQUIRED_STRATA:
                if not _non_empty_text(strata.get(key)):
                    problems.append(f"{prefix} {case_id!r}: strata.{key} is required")
        sensitive_key = _has_sensitive_key(redacted_input)
        if sensitive_key:
            problems.append(f"{prefix} {case_id!r}: input contains sensitive key {sensitive_key!r}")
        # The 50-case gate is intentionally a human-data gate.  Synthetic,
        # public and fixture rows can exercise the validator but never count
        # toward an independent human gold set, even if a caller forgets to
        # add counted=false.
        is_fixture = not source_kind_valid or source_kind != "human" or row.get("counted") is False
        if source_kind == "fixture" and row.get("counted") is not False:
            problems.append(f"{prefix} {case_id!r}: fixture records must set counted=false")
        if not is_fixture and isinstance(strata, Mapping):
            counted.append(row)

    for case_id in sorted({item for item in ids if ids.count(item) > 1}):
        problems.append(f"duplicate caseId: {case_id}")
    if len(counted) < min_counted_cases:
        problems.append(f"counted case coverage is {len(counted)}, requires at least {min_counted_cases}")

    dimension_counts = {dimension: sum(row.get("taskDimension") == dimension for row in counted) for dimension in required}
    for dimension, count in dimension_counts.items():
        if count < min_cases_per_dimension:
            problems.append(f"task dimension coverage is insufficient for {dimension}: {count}, requires {min_cases_per_dimension}")
    strata_counts: dict[str, int] = {}
    for key in REQUIRED_STRATA:
        values = {
            str(row["strata"].get(key))
            for row in counted
            if isinstance(row.get("strata"), Mapping) and row["strata"].get(key)
        }
        strata_counts[key] = len(values)
        if len(values) < 2:
            problems.append(f"strata coverage is insufficient for {key}: need at least 2 values")
    return BenchmarkValidation(tuple(problems), len(counted), dimension_counts, strata_counts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate human gold-standard benchmark JSONL.")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        result = validate_records(load_jsonl(args.path))
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "problems": [str(error)]}, ensure_ascii=False))
        return 2
    print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
