"""Backward-compatible import path for tutoring policy."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("domain.tutoring.turn_plan")

