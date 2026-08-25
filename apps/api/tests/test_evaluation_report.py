"""开发期只读报告的验收测试。

三条约定：
1. 严格只读——不写任何文件、不修改登记簿；
2. 产物缺失时返回 available=False 并给出下一步提示，而不是抛错；
3. 登记簿一致性问题时在报告中显式告警。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evaluation.report import collect_status, format_status


def _seed(directory: Path) -> None:
    # 登记簿
    (directory / "badcases.json").write_text(json.dumps({
        "version": 1,
        "badcases": [
            {"id": "b-open", "label": "question-number-boundary", "title": "开放缺陷",
             "stage": "segmentation", "status": "open"},
            {"id": "b-fixed", "label": "duplicate-key", "title": "已修缺陷",
             "stage": "storage", "status": "fixed",
             "resolutionNote": "已隔离", "fixedRelease": "0.21.1"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    # 重放报告
    (directory / "replay-report.json").write_text(json.dumps({
        "generatedAt": "2026-08-23T00:00:00+00:00",
        "totals": {"passed": 5, "failedUnexpected": 0, "knownBugSignatureChanged": 0},
    }), encoding="utf-8")
    # Judge 报告
    judge_dir = directory / "judge"
    judge_dir.mkdir()
    (judge_dir / "judge-ollama-qwen2.5:7b.json").write_text(json.dumps({
        "provider": "ollama", "model": "qwen2.5:7b",
        "totals": {"samples": 3, "judged": 3, "failed": 0},
        "results": [
            {"id": "s-1", "passed": True,
             "outcome": {"scores": {"clarity": 4, "targeting": 3, "factual": 5},
                          "rationale": "x", "confidence": 0.8}},
            {"id": "s-2", "passed": True,
             "outcome": {"scores": {"clarity": 5, "targeting": 4, "factual": 5},
                          "rationale": "y", "confidence": 0.9}},
        ],
    }, ensure_ascii=False), encoding="utf-8")


class CollectStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.output_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        _seed(self.output_dir)

    def test_aggregates_all_three_sources(self) -> None:
        status = collect_status(self.output_dir, self.output_dir)
        self.assertTrue(status["badcase"]["available"])
        self.assertEqual(status["badcase"]["total"], 2)
        self.assertEqual(sorted(status["badcase"]["byStatus"].keys()), ["fixed", "open"])
        self.assertEqual(status["replay"]["totals"]["passed"], 5)
        self.assertEqual(status["judge"]["averageScores"]["clarity"], 4.5)

    def test_missing_products_report_unavailable_with_hint(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            status = collect_status(empty, Path(empty))
            self.assertFalse(status["badcase"]["available"])
            self.assertFalse(status["replay"]["available"])
            self.assertIn("evaluation.replay", status["replay"]["hint"])

    def test_registry_problems_surface_in_report(self) -> None:
        # 破坏登记簿一致性：fixed 缺修复证据
        path = self.output_dir / "badcases.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["badcases"][1].pop("resolutionNote")
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        status = collect_status(self.output_dir, self.output_dir)
        self.assertTrue(status["badcase"]["registryProblems"])

    def test_format_contains_sections(self) -> None:
        status = collect_status(self.output_dir, self.output_dir)
        text = format_status(status)
        self.assertIn("Badcase 登记簿", text)
        self.assertIn("确定性重放", text)
        self.assertIn("LLM-as-Judge", text)
        self.assertIn("b-open", text)


if __name__ == "__main__":
    unittest.main()
