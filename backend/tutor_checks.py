"""Backward-compatible import path for tutoring checks."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("domain.tutoring.checks")

