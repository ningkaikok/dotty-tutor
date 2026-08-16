"""Backward-compatible import path for the model runtime adapter."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("infrastructure.runtime.model_runtime")

