"""金标准语料的自验证测试。

语料本身必须是回归资产：任何一条目失败都意味着有人改动了它所固化的行为。
这条规则同时约束"修复已知缺陷的人"——修好后必须回来把特征化条目转正，
否则 CI 不通过，防止缺陷被无声改变或无声消失。
"""

from __future__ import annotations

import unittest

from evaluation import corpus as corpus_module
from evaluation.judge import build_judge_prompt
from evaluation.replay import run_replay, write_report


class ExplanationSampleLabelTests(unittest.TestCase):
    """事实性标注是 ``factual`` 维度可度量的前提，必须自成回归资产。"""

    def test_sample_ids_are_unique(self) -> None:
        ids = [sample["id"] for sample in corpus_module.EXPLANATION_SAMPLES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_sample_declares_a_valid_factual_label(self) -> None:
        for sample in corpus_module.EXPLANATION_SAMPLES:
            with self.subTest(sample=sample["id"]):
                self.assertIn(sample["factualLabel"], corpus_module._FACTUAL_LABELS)

    def test_flaw_note_is_present_exactly_for_flawed_samples(self) -> None:
        """植入的错误必须写清楚是什么，否则后来的人无法判断评审模型漏了哪一处。"""
        for sample in corpus_module.EXPLANATION_SAMPLES:
            with self.subTest(sample=sample["id"]):
                flawed = sample["factualLabel"] == "flawed"
                self.assertEqual(flawed, bool(sample.get("flawNote", "").strip()))

    def test_both_label_classes_are_represented(self) -> None:
        """只有正确样本的语料上，factual 维度无法证伪——这正是旧语料的问题。"""
        labels = list(corpus_module.factual_labels().values())
        self.assertGreaterEqual(labels.count("sound"), 2)
        self.assertGreaterEqual(labels.count("flawed"), 2)

    def test_labels_never_reach_the_judge_prompt(self) -> None:
        """标注泄漏进提示词会让区分度指标失去意义：评审只是照抄答案。"""
        for sample in corpus_module.EXPLANATION_SAMPLES:
            prompt = build_judge_prompt(sample["questionContext"], sample["explanation"])
            for key, value in sample.items():
                if key in {"questionContext", "explanation"}:
                    continue
                with self.subTest(sample=sample["id"], field=key):
                    self.assertNotIn(value, prompt)


class CorpusIntegrityTests(unittest.TestCase):
    def test_entry_ids_are_unique(self) -> None:
        ids = [entry["id"] for entry in corpus_module.CORPUS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_entry_has_description_and_kind_specific_payload(self) -> None:
        for entry in corpus_module.CORPUS:
            with self.subTest(entry=entry["id"]):
                self.assertTrue(entry["description"].strip())
                kind = entry.get("kind", "segmentation")
                if kind == "segmentation":
                    self.assertTrue(entry["tags"])
                    self.assertTrue(entry.get("expect"))
                    self.assertTrue(entry.get("ocr_markdown"))
                elif kind == "formula-normalize":
                    self.assertTrue(entry.get("cases"))
                    for case in entry["cases"]:
                        self.assertIn("raw", case)
                        self.assertIn("expected", case)
                elif kind == "quality-gate":
                    self.assertIn("payload", entry)
                    self.assertIn("sourceBlock", entry)
                    self.assertIn("status", entry.get("expect", {}))
                elif kind == "turn-plan-intent":
                    for case in entry["cases"]:
                        self.assertIn("intentId", case)


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
