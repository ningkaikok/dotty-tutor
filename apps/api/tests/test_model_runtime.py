from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from infrastructure.runtime.model_runtime import (
    ModelRuntime,
    codex_command,
    codex_models,
)


class CodexCommandTests(unittest.TestCase):
    def test_default_catalog_includes_supported_subscription_models(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(codex_models(), [
                "default", "gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.5", "gpt-5.4",
            ])

    def test_catalog_can_be_limited_by_environment(self) -> None:
        with patch.dict(os.environ, {"CODEX_MODELS": "default, gpt-5.6-sol"}, clear=True):
            self.assertEqual(codex_models(), ["default", "gpt-5.6-sol"])

    def test_defaults_to_path_available_in_desktop_install(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(codex_command().endswith("/codex"))

    def test_respects_override_for_bundled_installs(self) -> None:
        bundled_path = "/Applications/ChatGPT.app/Contents/Resources/codex"
        with patch.dict(os.environ, {"CODEX_COMMAND": bundled_path}, clear=True):
            self.assertEqual(codex_command(), bundled_path)

    def test_tutor_runtime_reads_independent_environment_prefix(self) -> None:
        with patch.dict(os.environ, {
            "MODEL_PROVIDER": "ollama",
            "MODEL_NAME": "qwen2.5:3b",
            "TUTOR_MODEL_PROVIDER": "codex",
            "TUTOR_MODEL_NAME": "gpt-5.6-sol",
        }, clear=True):
            content_runtime = ModelRuntime()
            tutor_runtime = ModelRuntime(env_prefix="TUTOR_")

        self.assertEqual((content_runtime.selection.provider, content_runtime.selection.model), ("ollama", "qwen2.5:3b"))
        self.assertEqual((tutor_runtime.selection.provider, tutor_runtime.selection.model), ("codex", "gpt-5.6-sol"))

    def test_explicit_review_run_contains_real_provider_metadata(self) -> None:
        runtime = ModelRuntime()
        payload = {"ok": True}
        usage = {
            "prompt_tokens": 12,
            "output_tokens": 7,
            "providerAttempts": 2,
            "schemaFallback": {"used": True, "reason": "RuntimeError"},
        }
        with patch.object(runtime, "_ollama_json", return_value=(payload, usage)):
            result, run = runtime.generate_json_as(
                "ollama", "qwen", "prompt", {"type": "object"}, max_tokens=10
            )
        self.assertEqual(result, payload)
        self.assertEqual(run["providerAttempts"], 2)
        self.assertEqual(run["usage"], {"promptTokens": 12, "outputTokens": 7})
        self.assertEqual(run["schemaFallback"]["used"], True)
        self.assertGreaterEqual(run["durationMs"], 0)
