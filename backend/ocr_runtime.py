"""Backward-compatible import path for the OCR runtime adapter."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("infrastructure.runtime.ocr_runtime")

