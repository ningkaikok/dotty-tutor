"""Backward-compatible import path for mistake contracts."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("domain.contracts.mistake")

