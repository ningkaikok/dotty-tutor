"""合成章节场景集与离线基线的用户可读验收行为。"""

from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from evaluation.chapter.baseline import (
    DEFAULT_MANIFEST_PATH,
    check_manifest,
    load_manifest,
    main,
    run_baseline,
)


class ChapterBaselineBehaviorTests(unittest.TestCase):
    """Given/When/Then 验收：场景身份与可见基线行为必须可审计。"""

    def test_user_runs_chapter_scenarios_then_each_case_shows_outcome_and_scope(self) -> None:
        # Given 仓库内只有声明为合成且待人工复核的场景
        manifest, errors = load_manifest(DEFAULT_MANIFEST_PATH)
        self.assertEqual(errors, [])

        # When 确定性重放数学与英语场景
        report = run_baseline(manifest)

        # Then 每个场景均有逐案结果，且报告限制真实反映离线范围
        self.assertEqual(len(report["cases"]), len(manifest["cases"]))
        self.assertIsNone(report["metrics"]["cost"])
        self.assertIn("split_question_sources", report["scope"])
        self.assertTrue(any(case["outcome"] == "known_failure" for case in report["cases"]))
        self.assertTrue(any(case["subject"] == "english" for case in report["cases"]))

    def test_user_checks_manifest_then_missing_metadata_or_coverage_is_rejected(self) -> None:
        # Given 完整场景清单
        manifest, errors = load_manifest(DEFAULT_MANIFEST_PATH)
        self.assertEqual(errors, [])
        broken = copy.deepcopy(manifest)
        broken["cases"][0].pop("sourcePage")
        broken["cases"] = [case for case in broken["cases"] if case["subject"] != "english"]

        # When 执行清单门禁
        findings = check_manifest(broken)

        # Then 报告指出缺失来源定位和英语覆盖
        self.assertTrue(any("sourcePage" in finding for finding in findings))
        self.assertTrue(any("english" in finding.lower() for finding in findings))

    def test_user_submits_duplicate_ids_or_forged_human_approval_then_check_rejects_them(self) -> None:
        # Given 两个具有相同 ID 的场景，且试图伪造人工批准
        manifest, _ = load_manifest(DEFAULT_MANIFEST_PATH)
        broken = copy.deepcopy(manifest)
        broken["cases"][1]["id"] = broken["cases"][0]["id"]
        broken["cases"][0]["reviewStatus"] = "human_approved"
        broken["cases"][0]["reviewer"] = "synthetic-person"
        broken["cases"][0]["humanApproved"] = True

        # When 运行元数据门禁
        findings = check_manifest(broken)

        # Then 重复 ID 和未授权的人工复核声明都会明确失败
        self.assertTrue(any("duplicate" in finding.lower() for finding in findings))
        self.assertTrue(any("human" in finding.lower() for finding in findings))

    def test_user_supplies_invalid_ocr_case_then_check_rejects_unsafe_input(self) -> None:
        # Given 场景的 OCR 输入不是字符串
        manifest, _ = load_manifest(DEFAULT_MANIFEST_PATH)
        broken = copy.deepcopy(manifest)
        math_case = next(case for case in broken["cases"] if case["subject"] == "math")
        math_case["input"]["ocrText"] = {"unexpected": "object"}

        # When 检查并执行场景
        findings = check_manifest(broken)
        report = run_baseline(broken)

        # Then 不调用生产链路，并将非法输入原因留在报告中
        self.assertTrue(any("ocrText" in finding for finding in findings))
        result = next(case for case in report["cases"] if case["id"] == math_case["id"])
        self.assertEqual(result["outcome"], "invalid")
        self.assertTrue(result["failures"])

    def test_user_writes_invalid_manifest_then_loader_returns_actionable_json_error(self) -> None:
        # Given 临时目录中的损坏 JSON
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text("{", encoding="utf-8")

            # When 载入清单
            manifest, errors = load_manifest(path)

        # Then 返回可读错误而不是异常堆栈
        self.assertEqual(manifest, {})
        self.assertTrue(any("JSON" in error for error in errors))

    def test_user_supplies_unbounded_or_non_finite_metadata_then_check_rejects_it(self) -> None:
        # Given otherwise valid cases with invalid JSON-like field types and coordinates
        manifest, errors = load_manifest(DEFAULT_MANIFEST_PATH)
        self.assertEqual(errors, [])
        broken = copy.deepcopy(manifest)
        broken["cases"][0]["subject"] = []
        broken["cases"][0]["sourcePage"] = True
        broken["cases"][0]["sourceRegion"] = {"x": float("nan"), "y": 0, "width": 1, "height": 1}

        # When the validator inspects the fixture
        findings = check_manifest(broken)

        # Then it reports type and coordinate errors without raising
        self.assertTrue(any("subject" in finding for finding in findings))
        self.assertTrue(any("sourcePage" in finding for finding in findings))
        self.assertTrue(any("sourceRegion" in finding for finding in findings))

    def test_user_runs_check_cli_with_missing_metadata_then_it_exits_nonzero(self) -> None:
        # Given a fixture file with one required provenance field removed
        manifest, errors = load_manifest(DEFAULT_MANIFEST_PATH)
        self.assertEqual(errors, [])
        manifest["cases"][0].pop("sourcePage")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")

            # When the user invokes the documented CI-style check
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["--manifest", str(path), "--check"])

        # Then the CLI returns a failing status and prints the cause
        self.assertEqual(exit_code, 1)
        self.assertIn("sourcePage", output.getvalue())

    def test_user_repairs_a_documented_math_defect_then_check_requires_rebaseline(self) -> None:
        # Given a known missing-page failure whose OCR is now repaired locally
        manifest, errors = load_manifest(DEFAULT_MANIFEST_PATH)
        self.assertEqual(errors, [])
        case = next(case for case in manifest["cases"] if case["id"] == "math-missing-page-06")
        case["input"]["ocrText"] = case["input"]["ocrText"].replace(
            "<!-- page 7 missing -->", "2. Recovered page:求 y=1 在 x=0 时的值。"
        )

        # When the existing known-failure baseline is replayed unchanged
        result = next(item for item in run_baseline(manifest)["cases"] if item["id"] == case["id"])

        # Then the disappeared failure is surfaced for deliberate rebaseline
        self.assertEqual(result["outcome"], "fail")
        self.assertIn("baseline signature changed", result["failures"])

    def test_user_requests_baseline_then_english_evidence_and_paraphrase_are_reported_separately(self) -> None:
        # Given 独立英语场景含错依据和可接受改写
        manifest, errors = load_manifest(DEFAULT_MANIFEST_PATH)
        self.assertEqual(errors, [])

        # When 执行英语基线
        report = run_baseline(manifest)
        english = {case["id"]: case for case in report["cases"] if case["subject"] == "english"}

        # Then 答案和依据分项呈现，合理改写按明确变体接受
        self.assertIn("answer", english["en-correct-answer-wrong-evidence"]["checks"])
        self.assertEqual(english["en-correct-answer-wrong-evidence"]["outcome"], "expected_rejection")
        self.assertFalse(english["en-correct-answer-wrong-evidence"]["checks"]["evidence"]["passed"])
        self.assertTrue(english["en-reasonable-paraphrase"]["checks"]["answer"]["passed"])


if __name__ == "__main__":
    unittest.main()
