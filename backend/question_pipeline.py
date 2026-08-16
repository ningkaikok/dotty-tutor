"""Backward-compatible import path for the question domain pipeline."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("domain.questions.pipeline")

