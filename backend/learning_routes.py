"""Backward-compatible import path for :mod:`api.routers.learning_routes`."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("api.routers.learning_routes")

