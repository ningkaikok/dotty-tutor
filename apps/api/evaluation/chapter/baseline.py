"""Synthetic chapter scenarios and a deterministic, production-code baseline.

The math path calls the existing OCR-source splitter only. English scenarios use
explicit synthetic answer/evidence assertions and do not claim semantic grading.

Run from ``apps/api`` with ``uv run python -m evaluation.chapter.baseline --check``.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path
from typing import Any

from domain.questions.source import split_question_sources

DEFAULT_MANIFEST_PATH = Path(__file__).with_name("manifest.json")
REQUIRED_SUBJECTS = {"math", "english"}
REQUIRED_SOURCE_KIND = "synthetic"
REQUIRED_REVIEW_STATUS = "pending_human_review"


def load_manifest(path: Path = DEFAULT_MANIFEST_PATH) -> tuple[dict[str, Any], list[str]]:
    """Load JSON without letting malformed fixture input escape as an exception."""
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(f"invalid number {token}")),
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {}, [f"manifest JSON could not be loaded: {exc}"]
    if not isinstance(value, dict):
        return {}, ["manifest root must be an object"]
    return value, []


def check_manifest(manifest: dict[str, Any]) -> list[str]:
    """Validate provenance, scenario coverage, and executable inputs."""
    findings: list[str] = []
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        return ["manifest cases must be a non-empty array"]
    if manifest.get("version") != "chapter-scenarios-v1":
        findings.append("manifest version must be chapter-scenarios-v1")
    for field in ("reviewers", "humanReviewers", "reviewedBy"):
        if manifest.get(field) not in (None, "", [], {}):
            findings.append(f"manifest.{field} cannot claim human review for synthetic fixtures")
    ids: set[str] = set()
    subjects: set[str] = set()
    math_tags: set[str] = set()
    english_tags: set[str] = set()
    for index, case in enumerate(cases):
        prefix = f"cases[{index}]"
        if not isinstance(case, dict):
            findings.append(f"{prefix} must be an object")
            continue
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            findings.append(f"{prefix}.id is required")
        elif case_id in ids:
            findings.append(f"duplicate case id: {case_id}")
        else:
            ids.add(case_id)
        subject = case.get("subject")
        if subject not in ("math", "english"):
            findings.append(f"{prefix}.subject must be math or english")
        else:
            subjects.add(subject)
            tags = case.get("tags")
            if not isinstance(tags, list) or not tags or any(
                not isinstance(tag, str) or not tag.strip() for tag in tags
            ):
                findings.append(f"{prefix}.tags must be a non-empty array of strings")
            else:
                (math_tags if subject == "math" else english_tags).update(tags)
        if case.get("sourceKind") != REQUIRED_SOURCE_KIND:
            findings.append(f"{prefix}.sourceKind must be synthetic")
        if case.get("reviewStatus") != REQUIRED_REVIEW_STATUS:
            findings.append(f"{prefix}.reviewStatus must be pending_human_review; human approval cannot be declared here")
        if case.get("counted") is not False:
            findings.append(f"{prefix}.counted must be false for synthetic cases")
        if case.get("reviewer") is not None:
            findings.append(f"{prefix}.reviewer must be null; do not fabricate human review")
        for field in ("humanApproved", "approvedBy", "reviewerId", "reviewDecision"):
            if case.get(field) not in (None, False, "", [], {}):
                findings.append(f"{prefix}.{field} cannot claim human approval for a synthetic fixture")
        review_claim = case.get("review")
        if isinstance(review_claim, dict) and review_claim.get("status") in ("approved", "reviewed"):
            findings.append(f"{prefix}.review cannot claim human approval for a synthetic fixture")
        source = case.get("source")
        if not isinstance(source, dict):
            findings.append(f"{prefix}.source metadata is required")
            source = {}
        for field in ("license", "version"):
            if not isinstance(source.get(field), str) or not source[field].strip():
                findings.append(f"{prefix}.source.{field} is required")
        if (
            not isinstance(case.get("sourcePage"), int)
            or isinstance(case.get("sourcePage"), bool)
            or case.get("sourcePage", 0) < 1
        ):
            findings.append(f"{prefix}.sourcePage must be a positive integer")
        region = case.get("sourceRegion")
        region_keys = ("x", "y", "width", "height")
        if not isinstance(region, dict) or not all(
            isinstance(region.get(key), (int, float))
            and not isinstance(region.get(key), bool)
            and math.isfinite(region[key])
            for key in region_keys
        ):
            findings.append(f"{prefix}.sourceRegion requires finite numeric x, y, width, and height")
        elif (
            region["x"] < 0
            or region["y"] < 0
            or region["width"] <= 0
            or region["height"] <= 0
            or region["x"] + region["width"] > 10_000
            or region["y"] + region["height"] > 10_000
        ):
            findings.append(f"{prefix}.sourceRegion must be positive and within the 10000x10000 page coordinate bound")
        expected = case.get("expected")
        if not isinstance(expected, dict) or not expected:
            findings.append(f"{prefix}.expected result is required")
        if case.get("expectedOutcome") not in ("pass", "known_failure", "expected_rejection"):
            findings.append(f"{prefix}.expectedOutcome must be pass, known_failure, or expected_rejection")
        if case.get("expectedOutcome") == "known_failure" and subject != "math":
            findings.append(f"{prefix}.known_failure is reserved for the math production splitter baseline")
        if case.get("expectedOutcome") == "expected_rejection" and subject != "english":
            findings.append(f"{prefix}.expected_rejection is reserved for English fixture assertions")
        if subject == "math":
            input_value = case.get("input")
            if not isinstance(input_value, dict) or not isinstance(input_value.get("ocrText"), str):
                findings.append(f"{prefix}.input.ocrText must be a string")
            elif len(input_value["ocrText"]) > 20_000:
                findings.append(f"{prefix}.input.ocrText exceeds 20000 characters")
            if isinstance(expected, dict):
                numbers = expected.get("questionNumbers")
                fragments = expected.get("requiredSourceFragments", [])
                images = expected.get("imagesByQuestion", {})
                if not isinstance(numbers, list) or any(not isinstance(item, str) for item in numbers):
                    findings.append(f"{prefix}.expected.questionNumbers must be an array of strings")
                if not isinstance(fragments, list) or any(not isinstance(item, str) for item in fragments):
                    findings.append(f"{prefix}.expected.requiredSourceFragments must be an array of strings")
                if not isinstance(images, dict) or any(
                    not isinstance(number, str)
                    or not isinstance(paths, list)
                    or any(not isinstance(item, str) for item in paths)
                    for number, paths in images.items()
                ):
                    findings.append(f"{prefix}.expected.imagesByQuestion must map strings to arrays of strings")
            signature = case.get("baselineSignature")
            signature_fields = {
                "questionNumbers",
                "imagesByQuestion",
                "requiredSourceFragmentsPresent",
            }
            if not isinstance(signature, dict):
                findings.append(f"{prefix}.baselineSignature is required for deterministic math replay")
            else:
                if not signature_fields.issubset(signature):
                    findings.append(f"{prefix}.baselineSignature is missing required observed fields")
                if not isinstance(signature.get("questionNumbers"), list) or any(
                    not isinstance(item, str) for item in signature.get("questionNumbers", [])
                ):
                    findings.append(f"{prefix}.baselineSignature.questionNumbers must be an array of strings")
                if not isinstance(signature.get("imagesByQuestion"), dict) or any(
                    not isinstance(number, str)
                    or not isinstance(paths, list)
                    or any(not isinstance(item, str) for item in paths)
                    for number, paths in signature.get("imagesByQuestion", {}).items()
                ):
                    findings.append(f"{prefix}.baselineSignature.imagesByQuestion must map strings to arrays of strings")
                present = signature.get("requiredSourceFragmentsPresent")
                if not isinstance(present, list) or any(not isinstance(item, str) for item in present):
                    findings.append(f"{prefix}.baselineSignature.requiredSourceFragmentsPresent must be an array of strings")
                if (
                    case.get("expectedOutcome") == "known_failure"
                    and isinstance(expected, dict)
                    and isinstance(expected.get("questionNumbers"), list)
                    and isinstance(expected.get("requiredSourceFragments", []), list)
                    and all(isinstance(item, str) for item in expected.get("requiredSourceFragments", []))
                    and isinstance(expected.get("imagesByQuestion", {}), dict)
                    and all(
                        isinstance(number, str)
                        and isinstance(paths, list)
                        and all(isinstance(item, str) for item in paths)
                        for number, paths in expected.get("imagesByQuestion", {}).items()
                    )
                    and isinstance(signature.get("questionNumbers"), list)
                    and isinstance(present, list)
                    and all(isinstance(item, str) for item in present)
                    and isinstance(signature.get("imagesByQuestion"), dict)
                    and all(
                        isinstance(number, str)
                        and isinstance(paths, list)
                        and all(isinstance(item, str) for item in paths)
                        for number, paths in signature.get("imagesByQuestion", {}).items()
                    )
                ):
                    has_known_product_failure = (
                        signature["questionNumbers"] != expected["questionNumbers"]
                        or set(present) != set(expected.get("requiredSourceFragments", []))
                        or any(
                            not set(paths).issubset(set(signature["imagesByQuestion"].get(number, [])))
                            for number, paths in expected.get("imagesByQuestion", {}).items()
                        )
                    )
                    if not has_known_product_failure:
                        findings.append(f"{prefix}.known_failure signature must preserve an observable product defect")
        else:
            input_value = case.get("input")
            if not isinstance(input_value, dict):
                findings.append(f"{prefix}.input must be an object")
            elif any(not isinstance(input_value.get(key), str) for key in ("providedAnswer", "providedEvidence")):
                findings.append(f"{prefix}.input.providedAnswer and providedEvidence must be strings")
            expected = case.get("expected")
            if isinstance(expected, dict) and (
                not isinstance(expected.get("acceptedAnswers"), list)
                or not expected.get("acceptedAnswers")
                or any(not isinstance(answer, str) or not answer.strip() for answer in expected.get("acceptedAnswers", []))
                or not isinstance(expected.get("evidence"), str)
            ):
                findings.append(f"{prefix}.expected requires acceptedAnswers and evidence")
            if isinstance(input_value, dict) and not isinstance(input_value.get("passage"), str):
                findings.append(f"{prefix}.input.passage must be a string")
            if case.get("expectedOutcome") == "expected_rejection" and (
                not isinstance(expected, dict)
                or expected.get("knownFailureCheck") not in ("answer", "evidence")
            ):
                findings.append(f"{prefix}.expected_rejection requires knownFailureCheck answer or evidence")
    if not REQUIRED_SUBJECTS.issubset(subjects):
        missing = sorted(REQUIRED_SUBJECTS - subjects)
        findings.append(f"missing required subject coverage: {', '.join(missing)}")
    if sum(case.get("subject") == "math" for case in cases if isinstance(case, dict)) not in range(10, 21):
        findings.append("math scenario count must be between 10 and 20")
    required_math = {"text_pdf_ocr", "scan_page_ocr", "chart", "formula", "missing_page", "wrong_image", "omitted_condition"}
    if not required_math.issubset(math_tags):
        findings.append(f"missing math coverage tags: {', '.join(sorted(required_math - math_tags))}")
    required_english = {"word_meaning", "reference", "explicit", "inference", "wrong_evidence", "paraphrase"}
    if not required_english.issubset(english_tags):
        findings.append(f"missing english coverage tags: {', '.join(sorted(required_english - english_tags))}")
    return findings


def _math_observation(case: dict[str, Any]) -> dict[str, Any]:
    source = case["input"]["ocrText"]
    blocks = split_question_sources(source)
    numbers = [number for number, _block, _images in blocks]
    images = {number: image_list for number, _block, image_list in blocks}
    required_fragments = case["expected"].get("requiredSourceFragments", [])
    present_fragments = [fragment for fragment in required_fragments if fragment in source]
    return {
        "questionNumbers": numbers,
        "imagesByQuestion": images,
        "requiredSourceFragmentsPresent": present_fragments,
    }


def _normalise_english(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", value.casefold()))


def _english_observation(case: dict[str, Any]) -> dict[str, Any]:
    expected = case["expected"]
    input_value = case["input"]
    accepted = {_normalise_english(answer) for answer in expected["acceptedAnswers"]}
    answer = _normalise_english(input_value["providedAnswer"])
    return {
        "answer": {
            "passed": answer in accepted,
            "acceptedAnswers": expected["acceptedAnswers"],
            "providedAnswer": input_value["providedAnswer"],
            "basis": "explicit synthetic answer variants; no general semantic model",
        },
        "evidence": {
            "passed": _normalise_english(input_value["providedEvidence"])
            == _normalise_english(expected["evidence"]),
            "expected": expected["evidence"],
            "provided": input_value["providedEvidence"],
        },
    }


def run_baseline(manifest: dict[str, Any]) -> dict[str, Any]:
    """Replay cases and report product outcomes separately from corpus integrity."""
    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    cases = manifest.get("cases")
    if not isinstance(cases, list):
        cases = []
    for case in cases:
        if not isinstance(case, dict):
            results.append({"id": None, "outcome": "invalid", "failures": ["case is not an object"]})
            continue
        case_id = case.get("id")
        result: dict[str, Any] = {
            "id": case_id,
            "subject": case.get("subject"),
            "expectedOutcome": case.get("expectedOutcome"),
            "failures": [],
        }
        try:
            if case.get("subject") == "math" and isinstance(case.get("input"), dict) and isinstance(case["input"].get("ocrText"), str):
                if not isinstance(case.get("expected"), dict):
                    raise TypeError("expected must be an object")
                observed = _math_observation(case)
                expected = case.get("expected", {})
                product_checks = {
                    "questionNumbers": observed["questionNumbers"] == expected.get("questionNumbers"),
                    "requiredSourceFragments": len(observed["requiredSourceFragmentsPresent"])
                    == len(expected.get("requiredSourceFragments", [])),
                    "imagesByQuestion": all(
                        set(observed["imagesByQuestion"].get(number, [])) >= set(paths)
                        for number, paths in expected.get("imagesByQuestion", {}).items()
                    ),
                }
                signature = case.get("baselineSignature", {})
                reproducible = all(observed.get(key) == value for key, value in signature.items())
                result.update({"observed": observed, "checks": product_checks, "baselineSignatureMatched": reproducible})
                product_failures = [name for name, passed in product_checks.items() if not passed]
                failures = list(product_failures)
                if not reproducible:
                    failures.append("baseline signature changed")
                result["failures"] = failures
                if case.get("expectedOutcome") == "known_failure":
                    if product_failures and reproducible:
                        result["outcome"] = "known_failure"
                    else:
                        if not product_failures:
                            result["failures"].append("declared math baseline failure did not reproduce")
                        result["outcome"] = "fail"
                elif not failures:
                    result["outcome"] = "pass"
                else:
                    result["outcome"] = "fail"
            elif case.get("subject") == "english" and isinstance(case.get("input"), dict):
                checks = _english_observation(case)
                expected_outcome = case.get("expectedOutcome")
                failed_checks = [name for name, check in checks.items() if not check["passed"]]
                expected_failure = case.get("expected", {}).get("knownFailureCheck")
                if expected_outcome == "pass":
                    failures = failed_checks
                elif expected_outcome == "expected_rejection":
                    failures = list(failed_checks)
                    if expected_failure not in failed_checks:
                        failures.append("declared English fixture rejection did not reproduce")
                else:
                    failures = list(failed_checks)
                    if expected_failure not in failed_checks:
                        failures.append("declared English baseline failure did not reproduce")
                result.update({"checks": checks, "failures": failures})
                if expected_outcome == "expected_rejection" and expected_failure in failed_checks:
                    result["outcome"] = "expected_rejection"
                elif expected_outcome == "known_failure" and expected_failure in failed_checks:
                    result["outcome"] = "known_failure"
                else:
                    result["outcome"] = "pass" if not failures else "fail"
            else:
                result.update({"outcome": "invalid", "failures": ["unsupported subject or invalid input"]})
        except Exception as exc:  # Invalid manifest values must become per-case findings.
            result.update({"outcome": "invalid", "failures": [f"case evaluation failed safely: {exc}" ]})
        results.append(result)
    elapsed = time.perf_counter() - started
    return {
        "manifestVersion": manifest.get("version"),
        "cases": results,
        "scope": "Synthetic fixtures only; math calls domain.questions.source.split_question_sources; English uses declared answer variants and evidence equality. No OCR, model, network, or database.",
        "coverage": {
            "mathCases": sum(item.get("subject") == "math" for item in results),
            "englishCases": sum(item.get("subject") == "english" for item in results),
            "mathProductionKnownFailures": sum(
                item.get("subject") == "math" and item.get("outcome") == "known_failure"
                for item in results
            ),
            "englishFixtureChecks": sum(item.get("subject") == "english" for item in results),
            "englishExpectedRejections": sum(item.get("outcome") == "expected_rejection" for item in results),
        },
        "metrics": {"elapsedSeconds": round(elapsed, 6), "cost": None},
    }


def _render_report(report: dict[str, Any]) -> str:
    lines = [
        f"Chapter baseline: {report.get('manifestVersion')}",
        f"Scope: {report['scope']}",
        f"Coverage: {json.dumps(report['coverage'], ensure_ascii=False, sort_keys=True)}",
        f"Metrics: {json.dumps(report['metrics'], ensure_ascii=False, sort_keys=True)}",
    ]
    for finding in report.get("manifestFindings", []):
        lines.append(f"Manifest finding: {finding}")
    for case in report["cases"]:
        lines.append(f"- {case.get('id')}: {case.get('outcome')} | failures={json.dumps(case.get('failures', []), ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay synthetic chapter baseline scenarios")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--check", action="store_true", help="validate manifest and fail on unexpected baseline drift")
    parser.add_argument("--json", action="store_true", help="print JSON report")
    parser.add_argument("--output", type=Path, help="also write the report outside or inside the workspace")
    args = parser.parse_args(argv)
    manifest, load_errors = load_manifest(args.manifest)
    findings = [*load_errors, *check_manifest(manifest)] if not load_errors else load_errors
    report = run_baseline(manifest)
    report["manifestFindings"] = findings
    payload = json.dumps(report, ensure_ascii=False, indent=2) if args.json else _render_report(report)
    print(payload, end="" if payload.endswith("\n") else "\n")
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    unexpected = any(case.get("outcome") in {"fail", "invalid"} for case in report["cases"])
    if args.check and (findings or unexpected):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
