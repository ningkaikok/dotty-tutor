"""Backward-compatible import path for the question application service."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("application.services.question_processing")

