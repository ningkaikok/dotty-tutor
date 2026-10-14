"""Read-only readiness diagnosis using the existing admission and gold contract.

The report deliberately contains counts, fixed reason codes and line numbers,
never case IDs, reviewer IDs, source references or learner text. Supplied review
records are checked in memory; this tool does not create an admitted dataset.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from evaluation.benchmark.admission import admit_case
from evaluation.benchmark.contract import (
    MIN_CASES_PER_DIMENSION,
    MIN_COUNTED_CASES,
    REQUIRED_STRATA,
    validate_records,
)

# Existing validators embed case IDs in prose. Keep their messages internal and
# translate only known reason fragments; malformed user values never reach logs.
_REASON_CODES = (
    ("snapshot", "approval_snapshot_stale"),
    ("independent people", "reviewers_not_independent"),
    ("reviewer must differ", "reviewers_not_independent"),
    ("identity", "reviewer_identity_unverified"),
    ("two independent human reviews", "two_reviews_required"),
    ("pinned hash and reference", "source_provenance_missing"),
    ("both humans must approve", "approval_incomplete"),
    ("timestamp", "review_timestamp_invalid"),
    ("admission metadata", "admission_metadata_mismatch"),
    ("duplicate caseId", "duplicate_case_id"),
    ("sensitive key", "sensitive_input_key"),
    ("taskDimension", "task_dimension_invalid"),
    ("strata.", "strata_value_missing"),
    ("strata must", "strata_invalid"),
    ("redacted input", "input_invalid"),
    ("expected must", "expected_invalid"),
    ("rubric must", "rubric_invalid"),
    ("invalid sourceKind", "source_kind_invalid"),
    ("license", "license_missing"),
    ("redaction explanation", "redaction_missing"),
    ("annotatorId", "review_metadata_missing"),
    ("reviewerId", "review_metadata_missing"),
    ("caseId is required", "case_id_missing"),
    ("fixture records", "fixture_count_claim_invalid"),
)


def _reason(error: str) -> str:
    return next((code for fragment, code in _REASON_CODES if fragment in error), "candidate_invalid")


def _coverage_problem(message: str) -> bool:
    return message.startswith(("counted case coverage", "task dimension coverage", "strata coverage"))


def diagnose_file(
    path: Path,
    *,
    identities: Mapping[str, Any] | None = None,
    reviews: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Inspect all nonempty lines, admit only in memory, and expose safe gaps."""
    raw = path.read_bytes()
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError("input_encoding_invalid") from error
    problems: list[dict[str, Any]] = []
    kinds: Counter[str] = Counter({kind: 0 for kind in ("synthetic", "public", "human", "fixture", "unknown")})
    human_rows: list[dict[str, Any]] = []
    nonhuman_rows: list[dict[str, Any]] = []
    human_lines: list[int] = []
    candidate_count = 0
    invalid_lines = 0
    nonempty_lines = 0
    for number, text in enumerate(lines, start=1):
        if not text.strip():
            continue
        nonempty_lines += 1
        try:
            row = json.loads(text)
        except json.JSONDecodeError:
            problems.append({"line": number, "code": "invalid_json"})
            invalid_lines += 1
            continue
        if not isinstance(row, dict):
            problems.append({"line": number, "code": "record_not_object"})
            invalid_lines += 1
            continue
        candidate_count += 1
        kind = row.get("sourceKind")
        kind = kind if isinstance(kind, str) and kind in kinds else "unknown"
        kinds[kind] += 1
        if kind == "human":
            case_id = row.get("caseId")
            if reviews is not None and isinstance(case_id, str) and case_id in reviews:
                try:
                    row = admit_case(row, reviews[case_id], identities or {})
                except (ValueError, TypeError, KeyError) as error:
                    problems.append({"line": number, "code": _reason(str(error))})
                    # A failed external review must not fall back to older embedded approvals.
                    row = {**row, "counted": False}
            human_rows.append(row)
            human_lines.append(number)
        else:
            nonhuman_rows.append({**row, "counted": False})
            validation = validate_records([{**row, "counted": False}], min_counted_cases=0,
                                          min_cases_per_dimension=0)
            for message in validation.problems:
                if _coverage_problem(message) or any(field in message for field in ("annotatorId", "reviewerId", "approvedAt")):
                    continue
                problems.append({"line": number, "code": _reason(message)})
    # Validate one complete ID namespace: public/synthetic aliases must also
    # disqualify conflicting human rows. Nonhuman draft diagnostics were handled
    # above without requiring approval metadata, but they remain in this pass
    # so the existing contract owns global duplicate exclusion and coverage.
    validation = validate_records([*human_rows, *nonhuman_rows], trusted_identities=identities or {})
    for message in validation.problems:
        if not _coverage_problem(message):
            problem: dict[str, Any] = {"code": _reason(message)}
            match = re.match(r"record (\d+)\b", message)
            if match:
                ordinal = int(match.group(1)) - 1
                if ordinal >= len(human_lines):
                    continue
                problem["line"] = human_lines[ordinal]
            problems.append(problem)
    ready = not problems and not any(_coverage_problem(message) for message in validation.problems)
    actions = []
    if invalid_lines or problems:
        actions.append("repair_input_or_evidence")
    if kinds["synthetic"] or kinds["public"] or kinds["fixture"]:
        actions.append("retain_nonhuman_as_development_material")
    if validation.counted_cases < MIN_COUNTED_CASES:
        actions.append("collect_deidentified_human_sources_and_two_verified_reviews")
    if any(value < MIN_CASES_PER_DIMENSION for value in validation.dimensions.values()) or any(value < 2 for value in validation.strata.values()):
        actions.append("fill_task_dimension_and_strata_gaps")
    if ready:
        actions.append("use_existing_admission_cli_for_new_dataset_then_formal_paired_protocol")
    return {
        "schemaVersion": 1,
        "inputSha256": hashlib.sha256(raw).hexdigest(),
        "candidateCount": candidate_count,
        "nonemptyLineCount": nonempty_lines,
        "invalidLineCount": invalid_lines,
        "sourceKinds": dict(kinds),
        "identityRegisterProvided": identities is not None,
        "externalReviewsProvided": reviews is not None,
        "validIndependentHumanCases": validation.counted_cases,
        "gates": {
            "minimumHumanCases": MIN_COUNTED_CASES,
            "minimumPerTaskDimension": MIN_CASES_PER_DIMENSION,
            "minimumValuesPerStratum": 2,
            "humanCountSatisfied": validation.counted_cases >= MIN_COUNTED_CASES,
            "dimensionCoverageSatisfied": all(value >= MIN_CASES_PER_DIMENSION for value in validation.dimensions.values()),
            "strataCoverageSatisfied": all(validation.strata[key] >= 2 for key in REQUIRED_STRATA),
        },
        "coverage": {"taskDimensions": validation.dimensions, "strataValueCounts": validation.strata},
        "gaps": {
            "humanCases": max(0, MIN_COUNTED_CASES - validation.counted_cases),
            "taskDimensions": {key: max(0, MIN_CASES_PER_DIMENSION - value) for key, value in validation.dimensions.items()},
            "strataValues": {key: max(0, 2 - value) for key, value in validation.strata.items()},
        },
        "problems": problems,
        "problemCounts": dict(Counter(problem["code"] for problem in problems)),
        "goldAdmissionReady": ready,
        "formalRankingAvailable": False,
        "formalComparisonStatus": "requires_existing_formal_protocol_and_complete_model_arms_and_blind_ratings",
        "nextActions": actions,
        "note": "只诊断候选和已提供证据；不生成审批、不修改材料、不调用模型。身份登记须由数据维护者独立核验。",
    }


def _mapping_file(path: Path | None, code: str) -> Mapping[str, Any] | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError(code) from None
    if not isinstance(value, dict):
        raise ValueError(code)
    return value


def main() -> int:
    """Print a diagnostic or exclusively create a new report, preserving all inputs."""
    parser = argparse.ArgumentParser(description="只读诊断人工金标准准备度，不创建审批或模型排名")
    parser.add_argument("candidates", type=Path)
    parser.add_argument("--identities", type=Path, help="数据维护者独立核验的身份登记 JSON")
    parser.add_argument("--reviews", type=Path, help="caseId 到已有两份审批记录的 JSON 映射")
    parser.add_argument("--out", type=Path, help="可选的新诊断报告；拒绝覆盖任何已有文件")
    args = parser.parse_args()
    try:
        identities = _mapping_file(args.identities, "identity_register_invalid")
        reviews = _mapping_file(args.reviews, "review_records_invalid")
        report = diagnose_file(args.candidates, identities=identities, reviews=reviews)
        payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.out:
            # Exclusive creation also protects input files, hard links and symlinks.
            with args.out.open("x", encoding="utf-8") as handle:
                handle.write(payload)
        print(payload, end="")
        return 0 if report["goldAdmissionReady"] else 1
    except FileExistsError:
        code = "output_exists_refusing_overwrite"
    except OSError:
        code = "input_or_output_unavailable"
    except ValueError as error:
        code = str(error) if str(error) in {"input_encoding_invalid", "identity_register_invalid", "review_records_invalid"} else "input_invalid"
    print(json.dumps({"ok": False, "problem": code, "formalRankingAvailable": False}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
