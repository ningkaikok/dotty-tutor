"""Backward-compatible import path for the tutoring persistence store."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("persistence.tutoring_store")

