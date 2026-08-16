"""Backward-compatible import path for domain question source helpers."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("domain.questions.source")

