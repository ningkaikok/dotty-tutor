"""Backward-compatible import path for tutoring orchestration."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("application.services.tutor_engine")

