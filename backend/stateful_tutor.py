"""Backward-compatible import path for the tutoring application service."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("application.services.stateful_tutor")

