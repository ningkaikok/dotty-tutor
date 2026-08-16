"""Backward-compatible import path for the optional Qwen TTS adapter."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("infrastructure.runtime.qwen_tts_service")

