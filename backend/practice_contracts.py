"""Backward-compatible import path for practice contracts."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("domain.contracts.practice")

