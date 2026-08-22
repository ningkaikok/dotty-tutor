"""模型能力目录与健康记录的单元测试。

三条不变量在这里固化：
1. 未知模型走保守默认（不编造能力标签和上下文上限）；
2. 健康状态只影响候选筛选，连续成功会复位，绝不改写历史选择；
3. ``providers()`` 的既有契约（``models`` 字符串列表）不被破坏。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from infrastructure.runtime.capabilities import (
    HEALTH_BOOK,
    ModelHealthBook,
    annotate_model_entry,
    capability_for,
    eligible_for_role,
)
from infrastructure.runtime.model_runtime import ModelRuntime


class CapabilityLookupTests(unittest.TestCase):
    def test_exact_match_wins_over_prefix(self) -> None:
        capability = capability_for("ollama", "deepseek-r1:1.5b")
        self.assertEqual(capability.display_name, "DeepSeek-R1 推理系列")
        self.assertIn("math", capability.capabilities)

    def test_unknown_ollama_tag_gets_conservative_default(self) -> None:
        """动态拉取的新 tag 不允许被编造出视觉/长上下文等能力。"""
        capability = capability_for("ollama", "llama3:8b")
        self.assertEqual(capability.capabilities, frozenset())
        self.assertEqual(capability.context_window, 0)
        self.assertIsNone(capability.fallback)

    def test_codex_subscription_models_have_vision_role(self) -> None:
        capability = capability_for("codex", "gpt-5.6-sol")
        self.assertIn("vision", capability.roles)
        self.assertIn("vision", capability.capabilities)
        self.assertEqual(capability.fallback, ("codex", "gpt-5.6-luna"))


class ModelHealthBookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.book = ModelHealthBook()

    def test_failures_below_threshold_stay_healthy(self) -> None:
        self.book.mark_failure("ollama", "qwen2.5:7b", "timeout once")
        snapshot = self.book.snapshot("ollama", "qwen2.5:7b")
        self.assertTrue(snapshot["healthy"])
        self.assertEqual(snapshot["consecutiveFailures"], 1)
        self.assertEqual(snapshot["lastFailureReason"], "timeout once")

    def test_consecutive_failures_flip_health_and_success_resets(self) -> None:
        for _ in range(ModelHealthBook.FAILURE_THRESHOLD):
            self.book.mark_failure("codex", "gpt-5.6-sol", "boom")
        self.assertFalse(self.book.snapshot("codex", "gpt-5.6-sol")["healthy"])
        self.book.mark_success("codex", "gpt-5.6-sol")
        snapshot = self.book.snapshot("codex", "gpt-5.6-sol")
        self.assertTrue(snapshot["healthy"])
        self.assertEqual(snapshot["consecutiveFailures"], 0)


class RoleFilterTests(unittest.TestCase):
    def test_unhealthy_or_wrong_role_candidates_are_filtered(self) -> None:
        entry = annotate_model_entry("ollama", "deepseek-r1:1.5b")
        self.assertTrue(eligible_for_role(entry, "tutoring"))
        # deepseek-r1 不是视觉模型
        self.assertFalse(eligible_for_role(entry, "vision"))
        for _ in range(ModelHealthBook.FAILURE_THRESHOLD):
            HEALTH_BOOK.mark_failure("ollama", "deepseek-r1:1.5b", "down")
        try:
            self.assertFalse(eligible_for_role(annotate_model_entry("ollama", "deepseek-r1:1.5b"), "tutoring"))
        finally:
            HEALTH_BOOK.mark_success("ollama", "deepseek-r1:1.5b")


class ProvidersDecorationTests(unittest.TestCase):
    def test_providers_keeps_models_contract_and_adds_details(self) -> None:
        runtime = ModelRuntime()
        with (
            patch.object(runtime, "ollama_models", return_value=(["qwen2.5:7b"], None)),
            patch("infrastructure.runtime.model_runtime.shutil.which", return_value="/fake/codex"),
        ):
            providers = runtime.providers()
        by_id = {item["id"]: item for item in providers}
        ollama = by_id["ollama"]
        # 既有契约：models 仍是字符串列表
        self.assertEqual(ollama["models"], ["qwen2.5:7b"])
        details = {item["name"]: item for item in ollama["modelDetails"]}
        entry = details["qwen2.5:7b"]
        self.assertEqual(entry["displayName"], "Qwen2.5 通用系列")
        self.assertEqual(entry["contextWindow"], 32768)
        self.assertIn("tutoring", entry["roles"])
        self.assertTrue(entry["health"]["healthy"])
        mock_details = by_id["mock"]["modelDetails"][0]
        self.assertEqual(mock_details["costTier"], "free")


if __name__ == "__main__":
    unittest.main()
