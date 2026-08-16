"""Backward-compatible import path for domain question contracts."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("domain.questions.contracts")

