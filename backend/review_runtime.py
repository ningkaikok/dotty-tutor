"""Backward-compatible import path for the review runtime adapter."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("infrastructure.runtime.review_runtime")

