from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from model_runtime import ModelRuntime, codex_command


class CodexCommandTests(unittest.TestCase):
    def test_defaults_to_bare_command_name(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(codex_command(), "codex")

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
