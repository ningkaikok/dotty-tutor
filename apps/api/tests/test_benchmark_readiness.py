"""Read-only preparation diagnostics must not fabricate evidence or reveal content."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evaluation.benchmark.admission import snapshot_hash
from evaluation.benchmark.contract import DEFAULT_TASK_DIMENSIONS
from evaluation.benchmark.readiness import diagnose_file


class BenchmarkReadinessAcceptanceTests(unittest.TestCase):
    def test_user_sees_synthetic_candidates_and_zero_human_gold(self) -> None:
        """Given generated candidates, When diagnosing, Then show the human gap without ranking."""
        path = Path(__file__).resolve().parents[1] / "evaluation/benchmark/review_queue/candidates.jsonl"
        result = diagnose_file(path)
        self.assertEqual(result["candidateCount"], 50)
        self.assertEqual(result["sourceKinds"]["synthetic"], 50)
        self.assertEqual(result["validIndependentHumanCases"], 0)
        self.assertEqual(result["gaps"]["humanCases"], 50)
        self.assertFalse(result["goldAdmissionReady"])
        self.assertFalse(result["formalRankingAvailable"])

    def test_user_receives_every_bad_line_without_exposing_student_or_identity_text(self) -> None:
        """Given invalid JSON and a non-object, When inspecting, Then diagnose safe line numbers."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "candidates.jsonl"
            path.write_text('{"caseId":"SECRET-STUDENT", bad}\n["SECRET-STUDENT"]\n' + json.dumps(_candidate(0)) + "\n")
            result = diagnose_file(path)
        self.assertEqual(result["nonemptyLineCount"], 3)
        self.assertEqual(result["invalidLineCount"], 2)
        self.assertEqual(result["candidateCount"], 1)
        self.assertIn("invalid_json", {problem["code"] for problem in result["problems"]})
        self.assertNotIn("SECRET-STUDENT", json.dumps(result))
        self.assertNotIn("SECRET-PROMPT", json.dumps(result))

    def test_user_cannot_reuse_source_approval_after_hash_changes_or_double_count_one_person(self) -> None:
        """Given stale source approval or two aliases, When diagnosing, Then neither counts."""
        row = _candidate(0)
        reviews = {row["caseId"]: _reviews(row)}
        changed = copy.deepcopy(row)
        changed["source"]["contentSha256"] = "b" * 64
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "candidates.jsonl"
            path.write_text(json.dumps(changed) + "\n")
            stale = diagnose_file(path, identities=_identities(), reviews=reviews)
            self.assertEqual(stale["validIndependentHumanCases"], 0)
            self.assertIn("approval_snapshot_stale", {p["code"] for p in stale["problems"]})
            path.write_text(json.dumps(row) + "\n")
            identities = _identities()
            identities["b"]["personKey"] = identities["a"]["personKey"]
            aliases = diagnose_file(path, identities=identities, reviews=reviews)
        self.assertEqual(aliases["validIndependentHumanCases"], 0)
        self.assertIn("reviewers_not_independent", {p["code"] for p in aliases["problems"]})

    def test_user_can_check_a_valid_prepared_set_without_creating_approval_or_ranking(self) -> None:
        """Given 50 already-approved distinct cases, When diagnosing, Then report admission readiness only."""
        rows = [_candidate(index) for index in range(50)]
        reviews = {row["caseId"]: _reviews(row) for row in rows}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "candidates.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            original = path.read_bytes()
            result = diagnose_file(path, identities=_identities(), reviews=reviews)
            self.assertEqual(path.read_bytes(), original)
        self.assertEqual(result["validIndependentHumanCases"], 50)
        self.assertTrue(result["goldAdmissionReady"], result["problems"])
        self.assertFalse(result["formalRankingAvailable"])

    def test_user_does_not_count_duplicated_approved_case_ids(self) -> None:
        """Given the same approved case twice, When diagnosing, Then neither copy counts."""
        row = _candidate(0)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "candidates.jsonl"
            path.write_text((json.dumps(row) + "\n") * 2)
            result = diagnose_file(path, identities=_identities(), reviews={row["caseId"]: _reviews(row)})
        self.assertEqual(result["validIndependentHumanCases"], 0)
        self.assertIn("duplicate_case_id", {problem["code"] for problem in result["problems"]})

    def test_user_cannot_count_a_human_case_whose_id_is_reused_by_public_or_synthetic_material(self) -> None:
        """Given 50 approved humans and a cross-source duplicate, When diagnosing, Then exclude the conflict."""
        for kind in ("public", "synthetic"):
            with self.subTest(source_kind=kind), tempfile.TemporaryDirectory() as folder:
                rows = [_candidate(index) for index in range(50)]
                reviews = {row["caseId"]: _reviews(row) for row in rows}
                duplicate = {**rows[0], "sourceKind": kind, "counted": False}
                path = Path(folder) / "candidates.jsonl"
                path.write_text("".join(json.dumps(row) + "\n" for row in [*rows, duplicate]))
                result = diagnose_file(path, identities=_identities(), reviews=reviews)
            self.assertEqual(result["validIndependentHumanCases"], 49)
            self.assertFalse(result["goldAdmissionReady"])
            self.assertEqual(result["gaps"]["humanCases"], 1)
            self.assertIn("duplicate_case_id", {problem["code"] for problem in result["problems"]})
            self.assertNotIn("case-0", json.dumps(result))

    def test_user_cannot_overwrite_inputs_or_existing_reports_and_invalid_auxiliary_json_is_safe(self) -> None:
        """Given malformed identity data or an input output alias, When running CLI, Then fail without mutation."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "candidates.jsonl"
            path.write_text(json.dumps(_candidate(0)) + "\n")
            identities = Path(folder) / "identities.json"
            identities.write_text('["SECRET-PERSON"]')
            before = path.read_bytes()
            bad = _cli(path, "--identities", str(identities))
            self.assertEqual(bad.returncode, 2)
            self.assertNotIn("SECRET-PERSON", bad.stdout + bad.stderr)
            identities.write_text('{"SECRET-PERSON": invalid}')
            bad_json = _cli(path, "--identities", str(identities))
            self.assertEqual(bad_json.returncode, 2)
            self.assertNotIn("SECRET-PERSON", bad_json.stdout + bad_json.stderr)
            same = _cli(path, "--out", str(path))
            self.assertEqual(same.returncode, 2)
            self.assertEqual(path.read_bytes(), before)
            report = Path(folder) / "new-report.json"
            prepared = _cli(path, "--out", str(report))
            self.assertEqual(prepared.returncode, 1)
            self.assertTrue(report.is_file())
            saved = report.read_bytes()
            rerun = _cli(path, "--out", str(report))
            self.assertEqual(rerun.returncode, 2)
            self.assertEqual(report.read_bytes(), saved)


def _cli(path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "evaluation.benchmark.readiness", str(path), *arguments],
                          capture_output=True, text=True, check=False)


def _candidate(index: int) -> dict:
    return {"caseId": f"case-{index}", "taskDimension": DEFAULT_TASK_DIMENSIONS[index % 6],
            "sourceKind": "human", "source": {"contentSha256": "a" * 64, "reference": f"private-{index}"},
            "redactedInput": {"prompt": "SECRET-PROMPT"}, "expected": {"pass": True},
            "rubric": {"accuracy": "original checked"}, "license": "internal-consent", "redaction": "deidentified",
            "strata": {"subject": "math" if index % 2 else "science", "gradeBand": "middle" if index % 2 else "high",
                       "difficulty": "easy" if index % 2 else "hard"}}


def _identities() -> dict:
    return {actor: {"identityVerified": True, "actorKind": "individual", "personKey": "private-" + actor,
                    "evidenceRef": "private-evidence-" + actor} for actor in ("a", "b")}


def _reviews(row: dict) -> list[dict]:
    return [{"actorId": actor, "snapshotSha256": snapshot_hash(row), "decision": "approve",
             "reviewedAt": "2026-10-12T00:00:00Z", "rationale": "private rationale"} for actor in ("a", "b")]
