"""Backward-compatible import path for lesson contracts."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("domain.contracts.lesson")

