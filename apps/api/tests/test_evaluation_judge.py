"""LLM-as-Judge 模块的验收测试。

三条边界：
1. rubric 提示词必须包含全部维度与锁定约束（版本化溯源的前提）；
2. 评审输出的校验是真正的门禁——分值越界/缺依据/置信度越界一律拒绝；
3. judge 失败只返回 None，绝不抛出影响调用方，也绝不改写讲解本身。
"""

from __future__ import annotations

import json
import unittest

from evaluation.judge import (
    JUDGE_PROMPT_VERSION,
    RUBRIC,
    build_judge_prompt,
    parse_judge_response,
    run_judge,
)


class BuildPromptTests(unittest.TestCase):
    def test_prompt_contains_all_rubric_dimensions_and_locks(self) -> None:
        prompt = build_judge_prompt("题目上下文", "讲解文本")
        for key, desc in RUBRIC.items():
            self.assertIn(key, prompt)
            self.assertIn(desc[:10], prompt)
        self.assertIn("1-5", prompt)
        self.assertIn("JSON Schema", prompt)
        # 输入被截断到安全长度，防止超长教材文本撑爆上下文。
        self.assertIn("题目上下文", prompt)

    def test_prompt_version_constant_is_declared(self) -> None:
        self.assertTrue(JUDGE_PROMPT_VERSION.startswith("judge-rubric-"))


class ParseJudgeResponseTests(unittest.TestCase):
    def test_valid_response_normalizes(self) -> None:
        content = json.dumps({
            "scores": {"clarity": 4, "targeting": 3, "factual": 5},
            "rationale": "步骤清晰但未针对符号错误。",
            "confidence": 0.8,
        })
        result = parse_judge_response(content)
        self.assertIsNotNone(result)
        self.assertEqual(result["scores"], {"clarity": 4, "targeting": 3, "factual": 5})
        self.assertEqual(result["confidence"], 0.8)
        self.assertEqual(result["judgePromptVersion"], JUDGE_PROMPT_VERSION)

    def test_out_of_range_score_rejected(self) -> None:
        content = json.dumps({
            "scores": {"clarity": 6, "targeting": 3, "factual": 5},
            "rationale": "x", "confidence": 0.5,
        })
        self.assertIsNone(parse_judge_response(content))

    def test_missing_rationale_rejected(self) -> None:
        content = json.dumps({
            "scores": {"clarity": 4, "targeting": 3, "factual": 5},
            "rationale": "  ", "confidence": 0.5,
        })
        self.assertIsNone(parse_judge_response(content))

    def test_confidence_out_of_range_rejected(self) -> None:
        content = json.dumps({
            "scores": {"clarity": 4, "targeting": 3, "factual": 5},
            "rationale": "x", "confidence": 1.5,
        })
        self.assertIsNone(parse_judge_response(content))

    def test_non_json_content_rejected(self) -> None:
        self.assertIsNone(parse_judge_response("模型输出了一段纯文本"))


class RunJudgeTests(unittest.TestCase):
    def _fake(self, payload=None, raising=False):
        def fake_generate(provider, model, prompt, schema, max_tokens=400):
            if raising:
                raise RuntimeError("模型不可用")
            return payload, {"prompt_tokens": 10, "output_tokens": 5}

        return fake_generate

    def test_success_returns_normalized_result(self) -> None:
        payload = {
            "scores": {"clarity": 5, "targeting": 4, "factual": 5},
            "rationale": "针对符号错误，步骤完整。",
            "confidence": 0.9,
        }
        result = run_judge(
            generate_json_as=self._fake(payload),
            provider="ollama", model="qwen2.5:7b",
            question_context="上下文", explanation="讲解",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["scores"]["clarity"], 5)

    def test_model_failure_returns_none_not_raise(self) -> None:
        result = run_judge(
            generate_json_as=self._fake(raising=True),
            provider="ollama", model="qwen2.5:7b",
            question_context="上下文", explanation="讲解",
        )
        self.assertIsNone(result)

    def test_invalid_model_output_returns_none(self) -> None:
        result = run_judge(
            generate_json_as=self._fake({"scores": {}}),
            provider="ollama", model="qwen2.5:7b",
            question_context="上下文", explanation="讲解",
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
