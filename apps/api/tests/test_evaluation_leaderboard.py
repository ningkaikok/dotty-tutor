"""多模型排行榜测试：评审一致性报告 + 生成质量对比报告。

两组测试分别验证两个不同的问题没有被混用：
1. run_judge_agreement 的"变量"必须是评审模型，讲解文本必须保持不变；
2. run_generation_comparison 的评审模型必须固定，只有生成模型在变化。
"""

from __future__ import annotations

import unittest

from evaluation.corpus import EXPLANATION_CORPUS_VERSION, EXPLANATION_SAMPLES
from evaluation.leaderboard import (
    GENERATION_COMPARISON_REPORT_KIND,
    JUDGE_AGREEMENT_REPORT_KIND,
    build_generation_prompt,
    generate_explanation,
    run_generation_comparison,
    run_judge_agreement,
    statistical_note,
)

VALID_JUDGE_PAYLOAD = {
    "scores": {"clarity": 4, "targeting": 3, "factual": 5},
    "rationale": "评分依据。",
    "confidence": 0.8,
}


def _judge_generator(scores_by_model: dict[str, dict[str, int]] | None = None):
    """伪造 generate_json_as：按 (provider, model) 返回固定评分或生成文本。"""

    def generate(provider, model, prompt, schema, max_tokens=400):
        run = {
            "durationMs": 12.0,
            "providerAttempts": 1,
            "schemaFallback": {"used": False, "reason": None},
            "usage": {"promptTokens": 8, "outputTokens": 4},
        }
        if "explanation" in schema.get("properties", {}):
            return {"explanation": f"{model} 生成的讲解文本"}, run
        scores = (scores_by_model or {}).get(f"{provider}:{model}") or VALID_JUDGE_PAYLOAD["scores"]
        return {**VALID_JUDGE_PAYLOAD, "scores": scores}, run

    return generate


class BuildGenerationPromptTests(unittest.TestCase):
    def test_prompt_asks_for_guidance_not_final_answer(self) -> None:
        prompt = build_generation_prompt("解方程 2x+1=5")
        self.assertIn("解方程 2x+1=5", prompt)
        self.assertIn("不要直接写出最终数值答案", prompt)
        self.assertIn("JSON Schema", prompt)


class GenerateExplanationTests(unittest.TestCase):
    def test_successful_generation_returns_stripped_text(self) -> None:
        def fake(provider, model, prompt, schema, max_tokens=300):
            return {"explanation": "  一段讲解  "}, {"durationMs": 5.0}

        result = generate_explanation(
            generate_json_as=fake, provider="ollama", model="qwen2.5:3b",
            question_context="题目",
        )
        self.assertEqual(result["explanation"], "一段讲解")
        self.assertIsNone(result["errorType"])

    def test_empty_explanation_is_reported_as_error(self) -> None:
        def fake(provider, model, prompt, schema, max_tokens=300):
            return {"explanation": "   "}, {"durationMs": 5.0}

        result = generate_explanation(
            generate_json_as=fake, provider="ollama", model="qwen2.5:3b",
            question_context="题目",
        )
        self.assertIsNone(result["explanation"])
        self.assertEqual(result["errorType"], "empty_generation")

    def test_provider_exception_does_not_propagate(self) -> None:
        def fake(provider, model, prompt, schema, max_tokens=300):
            raise RuntimeError("provider unavailable")

        result = generate_explanation(
            generate_json_as=fake, provider="ollama", model="qwen2.5:3b",
            question_context="题目",
        )
        self.assertIsNone(result["explanation"])
        self.assertEqual(result["errorType"], "RuntimeError")


class RunJudgeAgreementTests(unittest.TestCase):
    def test_requires_at_least_two_judges(self) -> None:
        with self.assertRaises(ValueError):
            run_judge_agreement([("ollama", "qwen2.5:7b")])

    def test_identical_scores_yield_zero_spread(self) -> None:
        report = run_judge_agreement(
            [("ollama", "qwen2.5:3b"), ("ollama", "qwen2.5:7b")],
            generate_json_as=_judge_generator(),
        )
        self.assertEqual(report["reportKind"], JUDGE_AGREEMENT_REPORT_KIND)
        self.assertEqual(report["corpusVersion"], EXPLANATION_CORPUS_VERSION)
        for spread in report["dimensionMeanSpread"].values():
            self.assertEqual(spread, 0.0)

    def test_disagreeing_judges_produce_nonzero_spread(self) -> None:
        report = run_judge_agreement(
            [("ollama", "qwen2.5:3b"), ("ollama", "qwen2.5:7b")],
            generate_json_as=_judge_generator({
                "ollama:qwen2.5:3b": {"clarity": 2, "targeting": 3, "factual": 5},
                "ollama:qwen2.5:7b": {"clarity": 5, "targeting": 3, "factual": 5},
            }),
        )
        self.assertEqual(report["dimensionMeanSpread"]["clarity"], 3.0)
        self.assertEqual(report["dimensionMeanSpread"]["targeting"], 0.0)

    def test_statistical_note_names_sample_count(self) -> None:
        report = run_judge_agreement(
            [("ollama", "qwen2.5:3b"), ("ollama", "qwen2.5:7b")],
            generate_json_as=_judge_generator(),
        )
        self.assertIn(f"N={len(EXPLANATION_SAMPLES)}", report["statisticalNote"])
        self.assertIn("不构成统计显著性", report["statisticalNote"])


class RunGenerationComparisonTests(unittest.TestCase):
    def test_judge_model_stays_fixed_across_generators(self) -> None:
        report = run_generation_comparison(
            [("ollama", "qwen2.5:3b"), ("ollama", "qwen2.5:7b")],
            judge_provider="codex", judge_model="default",
            generate_json_as=_judge_generator(),
        )
        self.assertEqual(report["reportKind"], GENERATION_COMPARISON_REPORT_KIND)
        self.assertEqual(report["judge"], {"provider": "codex", "model": "default"})
        self.assertEqual(
            set(report["perGeneratorMetrics"]),
            {"ollama:qwen2.5:3b", "ollama:qwen2.5:7b"},
        )

    def test_each_generator_scored_by_the_same_fixed_judge(self) -> None:
        report = run_generation_comparison(
            [("ollama", "qwen2.5:3b")],
            judge_provider="codex", judge_model="default",
            generate_json_as=_judge_generator(),
        )
        metrics = report["perGeneratorMetrics"]["ollama:qwen2.5:3b"]
        self.assertEqual(metrics["sampleCount"], len(EXPLANATION_SAMPLES))
        self.assertEqual(metrics["judged"], len(EXPLANATION_SAMPLES))
        self.assertEqual(metrics["successRate"], 1.0)
        self.assertIn("clarity", metrics["averageScores"])

    def test_generation_failure_skips_judge_and_is_explicit(self) -> None:
        def fake_generate(provider, model, prompt, schema, max_tokens=300):
            if "explanation" in schema.get("properties", {}):
                return {"explanation": ""}, {"durationMs": 1.0}
            return VALID_JUDGE_PAYLOAD, {"durationMs": 1.0}

        report = run_generation_comparison(
            [("ollama", "qwen2.5:3b")],
            judge_provider="codex", judge_model="default",
            generate_json_as=fake_generate,
        )
        metrics = report["perGeneratorMetrics"]["ollama:qwen2.5:3b"]
        self.assertEqual(metrics["generated"], 0)
        self.assertEqual(metrics["judged"], 0)
        self.assertEqual(metrics["successRate"], 0.0)
        for result in metrics["results"]:
            self.assertFalse(result["generated"])
            self.assertEqual(result["errorType"], "empty_generation")

    def test_statistical_note_present(self) -> None:
        report = run_generation_comparison(
            [("ollama", "qwen2.5:3b")],
            judge_provider="codex", judge_model="default",
            generate_json_as=_judge_generator(),
        )
        self.assertEqual(statistical_note(len(EXPLANATION_SAMPLES)), report["statisticalNote"])


if __name__ == "__main__":
    unittest.main()
