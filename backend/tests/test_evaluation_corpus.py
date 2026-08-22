"""金标准语料的自验证测试。

语料本身必须是回归资产：任何一条目失败都意味着有人改动了它所固化的行为。
这条规则同时约束"修复已知缺陷的人"——修好后必须回来把特征化条目转正，
否则 CI 不通过，防止缺陷被无声改变或无声消失。
"""

from __future__ import annotations

import unittest

from evaluation import corpus as corpus_module
from evaluation.replay import run_replay, write_report


class CorpusIntegrityTests(unittest.TestCase):
    def test_entry_ids_are_unique(self) -> None:
        ids = [entry["id"] for entry in corpus_module.CORPUS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_entry_has_description_tags_and_expectations(self) -> None:
        for entry in corpus_module.CORPUS:
            with self.subTest(entry=entry["id"]):
                self.assertTrue(entry["description"].strip())
                self.assertTrue(entry["tags"])
                self.assertTrue(entry.get("expect"))
                self.assertIn(entry["ocr_markdown"], (None, entry["ocr_markdown"]))


class ReplayBehaviorTests(unittest.TestCase):
    def test_all_regular_entries_pass_against_current_pipeline(self) -> None:
        result = run_replay()
        unexpected = [
            entry
            for entry in result["entries"]
            if not entry["passed"] and not entry["documenting_bug"]
        ]
        self.assertEqual(unexpected, [], f"unexpected replay failures: {unexpected}")

    def test_known_bug_entries_still_reproduce_their_signature(self) -> None:
        """特征化条目必须继续复现缺陷：缺陷被修复时应显式失败并要求转正语料。"""
        result = run_replay()
        changed = [
            entry for entry in result["entries"]
            if entry["documenting_bug"] and not entry["passed"]
        ]
        self.assertEqual(
            changed,
            [],
            f"documented bug behavior changed, corpus needs updating: {changed}",
        )
        documented = [e for e in result["entries"] if e["documenting_bug"]]
        self.assertTrue(documented, "corpus must keep at least one documented bug entry")

    def test_report_writer_produces_json_and_markdown(self) -> None:
        import tempfile
        from pathlib import Path

        result = run_replay()
        with tempfile.TemporaryDirectory() as directory:
            paths = write_report(result, Path(directory))
            self.assertTrue(paths["json"].exists())
            markdown = paths["markdown"].read_text(encoding="utf-8")
        self.assertIn("离线评测重放报告", markdown)
        self.assertIn("caption-attribution-real-excerpt", markdown)


if __name__ == "__main__":
    unittest.main()
