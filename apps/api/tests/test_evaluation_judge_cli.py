"""Judge 报告契约、聚合指标和历史报告保留策略测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import evaluation.judge_cli as judge_cli
from evaluation.corpus import EXPLANATION_SAMPLES, flaw_families
from evaluation.judge_cli import (
    JUDGE_REPORT_KIND,
    JUDGE_REPORT_VERSION,
    run_all,
    validate_report,
    write_report,
)

VALID_PAYLOAD = {
    "scores": {"clarity": 4, "targeting": 3, "factual": 5},
    "rationale": "评分依据。",
    "confidence": 0.8,
}


def _generator(*, attempts: int = 2, fallback: bool = True, fail_on: int | None = None):
    calls = 0

    def generate(provider, model, prompt, schema, max_tokens=400):
        nonlocal calls
        calls += 1
        if calls == fail_on:
            raise RuntimeError("provider unavailable")
        return VALID_PAYLOAD, {
            "durationMs": 17.5,
            "providerAttempts": attempts,
            "schemaFallback": {"used": fallback, "reason": "grammar" if fallback else None},
            "usage": {"promptTokens": 10, "outputTokens": 6},
        }

    return generate


def _label_aware_generator(*, sound_factual: int = 5, flawed_factual: int = 2,
                           fail_on_flawed: bool = False):
    """按提示词里出现的讲解原文判断样本类别，模拟"看得出错"的评审模型。

    伪造的评审拿不到标注（标注不进提示词），只能靠讲解原文反查——这与真实评审
    模型面对的输入完全一致，避免测试用一条真实链路上不存在的捷径来造分。
    """
    by_explanation = {
        sample["explanation"]: sample["factualLabel"] for sample in EXPLANATION_SAMPLES
    }

    def generate(provider, model, prompt, schema, max_tokens=400):
        label = next(
            (value for text, value in by_explanation.items() if text in prompt),
            "sound",
        )
        if label == "flawed" and fail_on_flawed:
            raise RuntimeError("provider unavailable")
        factual = flawed_factual if label == "flawed" else sound_factual
        return (
            {**VALID_PAYLOAD, "scores": {"clarity": 4, "targeting": 3, "factual": factual}},
            {
                "durationMs": 12.0,
                "providerAttempts": 1,
                "schemaFallback": {"used": False, "reason": None},
                "usage": {"promptTokens": 10, "outputTokens": 6},
            },
        )

    return generate


def _family_aware_generator(*, sound_factual: int = 5, family_factual: dict[str, int] | None = None):
    """按错误家族分别指定 factual 分数，用于验证 ``byFlawFamily`` 是否按家族区分。

    未在 ``family_factual`` 里指定的家族默认打 3 分（既不是"完全没看出来"的 5，
    也不是"看出来了"的低分），避免测试意外依赖某个家族的默认行为。
    """
    families = flaw_families()
    by_explanation_family = {
        sample["explanation"]: families.get(sample["id"]) for sample in EXPLANATION_SAMPLES
    }
    family_factual = family_factual or {}

    def generate(provider, model, prompt, schema, max_tokens=400):
        family = next(
            (value for text, value in by_explanation_family.items() if text in prompt),
            None,
        )
        factual = sound_factual if family is None else family_factual.get(family, 3)
        return (
            {**VALID_PAYLOAD, "scores": {"clarity": 4, "targeting": 3, "factual": factual}},
            {
                "durationMs": 12.0,
                "providerAttempts": 1,
                "schemaFallback": {"used": False, "reason": None},
                "usage": {"promptTokens": 10, "outputTokens": 6},
            },
        )

    return generate


class ScoreDiscriminationTests(unittest.TestCase):
    """区分度回答的是"评审能不能看出错"，一致性指标回答不了这个问题。"""

    def test_gap_reflects_lower_factual_scores_on_flawed_samples(self) -> None:
        report = run_all("ollama", "qwen2.5:7b", generate_json_as=_label_aware_generator())
        discrimination = report["judgeMetrics"]["scoreDiscrimination"]
        self.assertGreater(discrimination["soundCount"], 0)
        self.assertGreater(discrimination["flawedCount"], 0)
        self.assertEqual(discrimination["byDimension"]["factual"]["gap"], 3.0)
        # 对照维度：植入的错误只在数学内容上，clarity/targeting 不应被拉开。
        self.assertEqual(discrimination["byDimension"]["clarity"]["gap"], 0.0)
        self.assertEqual(discrimination["byDimension"]["targeting"]["gap"], 0.0)

    def test_uniform_scores_yield_zero_gap_rather_than_none(self) -> None:
        """0 是"测到了、没有区分能力"，None 是"没测成"，两者不能混。"""
        report = run_all(
            "ollama", "qwen2.5:7b",
            generate_json_as=_label_aware_generator(sound_factual=5, flawed_factual=5),
        )
        self.assertEqual(
            report["judgeMetrics"]["scoreDiscrimination"]["byDimension"]["factual"]["gap"],
            0.0,
        )

    def test_gap_is_none_when_one_class_has_no_successful_judge(self) -> None:
        report = run_all(
            "ollama", "qwen2.5:7b",
            generate_json_as=_label_aware_generator(fail_on_flawed=True),
        )
        discrimination = report["judgeMetrics"]["scoreDiscrimination"]
        self.assertEqual(discrimination["flawedCount"], 0)
        self.assertIsNone(discrimination["byDimension"]["factual"]["gap"])
        self.assertIsNone(discrimination["byDimension"]["factual"]["flawedMean"])
        self.assertIsNotNone(discrimination["byDimension"]["factual"]["soundMean"])

    def test_report_contract_requires_score_discrimination(self) -> None:
        report = run_all("ollama", "qwen2.5:7b", generate_json_as=_label_aware_generator())
        self.assertEqual(validate_report(report), [])
        del report["judgeMetrics"]["scoreDiscrimination"]
        self.assertIn(
            "judgeMetrics.scoreDiscrimination is required", validate_report(report)
        )


class FlawFamilyDiscriminationTests(unittest.TestCase):
    """整体 flawed gap 会把"编造通则"和"算错一步"平均在一起，可能互相掩盖。

    这组测试验证 byFlawFamily 能把两者分开：一个评审模型完全漏检某个家族时，
    整体 gap 依然可能显得体面，但对应家族的 gap 必须显式地接近 0。
    """

    def test_family_breakdown_isolates_the_family_that_is_not_caught(self) -> None:
        report = run_all(
            "ollama", "qwen2.5:7b",
            generate_json_as=_family_aware_generator(
                sound_factual=5,
                family_factual={
                    "fabricated-rule": 5,       # 完全没看出来：和 sound 打一样的分
                    "computation-error": 1,     # 看出来了
                    "fabricated-condition": 1,
                    "definition-error": 1,
                },
            ),
        )
        by_family = report["judgeMetrics"]["scoreDiscrimination"]["byFlawFamily"]
        self.assertEqual(by_family["fabricated-rule"]["byDimension"]["factual"]["gap"], 0.0)
        self.assertEqual(by_family["computation-error"]["byDimension"]["factual"]["gap"], 4.0)
        self.assertEqual(by_family["fabricated-condition"]["byDimension"]["factual"]["gap"], 4.0)

    def test_family_sample_counts_match_the_corpus(self) -> None:
        report = run_all(
            "ollama", "qwen2.5:7b",
            generate_json_as=_family_aware_generator(family_factual={}),
        )
        by_family = report["judgeMetrics"]["scoreDiscrimination"]["byFlawFamily"]
        # 语料测试那边锁的是 >=5；这里锁精确值，两处一起变化时能互相提醒。
        self.assertEqual(by_family["fabricated-rule"]["sampleCount"], 6)
        self.assertEqual(by_family["computation-error"]["sampleCount"], 3)

    def test_family_absent_when_every_judge_call_fails_for_it(self) -> None:
        """一个家族全军覆没时不该出现在结果里，也不该被静默算成 0 分。"""
        def always_fail_generate(provider, model, prompt, schema, max_tokens=400):
            raise RuntimeError("provider unavailable")

        report = run_all("ollama", "qwen2.5:7b", generate_json_as=always_fail_generate)
        by_family = report["judgeMetrics"]["scoreDiscrimination"]["byFlawFamily"]
        self.assertEqual(by_family, {})


class JudgeReportTests(unittest.TestCase):
    def test_report_contract_separates_judge_metrics_and_runtime_metadata(self) -> None:
        report = run_all("ollama", "qwen", generate_json_as=_generator())
        self.assertEqual(validate_report(report), [])
        self.assertEqual(report["reportKind"], JUDGE_REPORT_KIND)
        self.assertEqual(report["reportVersion"], JUDGE_REPORT_VERSION)
        self.assertEqual(len(report["sampleSetHash"]), 64)
        self.assertEqual(report["judge"], {
            "provider": "ollama", "model": "qwen", "promptVersion": report["judge"]["promptVersion"]
        })
        sample = report["results"][0]
        self.assertEqual(sample["logicalCalls"], 1)
        self.assertEqual(sample["providerAttempts"], 2)
        self.assertEqual(sample["tokenUsage"], {"promptTokens": 10, "outputTokens": 6})
        self.assertEqual(report["judgeMetrics"]["providerAttempts"], 2 * len(EXPLANATION_SAMPLES))
        self.assertEqual(report["judgeMetrics"]["p50DurationMs"], 17.5)
        self.assertNotIn("rationale", str(report))
        self.assertNotIn("modelMetrics", report)

    def test_failed_judge_is_explicit_and_checkable(self) -> None:
        report = run_all("ollama", "qwen", generate_json_as=_generator(fail_on=2))
        self.assertEqual(report["judgeMetrics"]["failureCount"], 1)
        self.assertEqual(report["results"][1]["judgeSucceeded"], False)
        self.assertEqual(validate_report(report), [])

    def test_invalid_report_contract_is_rejected(self) -> None:
        report = run_all("ollama", "qwen", generate_json_as=_generator())
        del report["results"][0]["providerAttempts"]
        problems = validate_report(report)
        self.assertIn("results[0].providerAttempts is invalid", problems)

    def test_report_writer_keeps_each_run(self) -> None:
        report = run_all("ollama", "qwen", generate_json_as=_generator())
        with tempfile.TemporaryDirectory() as directory:
            first = write_report(report, Path(directory))
            second_report = run_all("ollama", "qwen", generate_json_as=_generator())
            second = write_report(second_report, Path(directory))
            self.assertNotEqual(first, second)
            self.assertEqual(len(list(Path(directory).glob("*.json"))), 2)

    def test_check_returns_failure_for_execution_failure(self) -> None:
        report = run_all("ollama", "qwen", generate_json_as=_generator(fail_on=1))
        with tempfile.TemporaryDirectory() as directory, patch(
            "sys.argv", ["judge_cli", "--check", "--output-dir", directory]
        ), patch.object(judge_cli, "run_all", return_value=report):
            self.assertEqual(judge_cli.main(), 1)


if __name__ == "__main__":
    unittest.main()
