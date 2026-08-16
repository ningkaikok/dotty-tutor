"""Backward-compatible import path for the variation persistence store."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("persistence.variation_store")

