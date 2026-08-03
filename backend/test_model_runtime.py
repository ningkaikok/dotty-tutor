from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from model_runtime import codex_command


class CodexCommandTests(unittest.TestCase):
    def test_defaults_to_bare_command_name(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(codex_command(), "codex")

    def test_respects_override_for_bundled_installs(self) -> None:
        bundled_path = "/Applications/ChatGPT.app/Contents/Resources/codex"
        with patch.dict(os.environ, {"CODEX_COMMAND": bundled_path}, clear=True):
            self.assertEqual(codex_command(), bundled_path)
