"""Backward-compatible import path for the mistake persistence store."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("persistence.mistake_store")

