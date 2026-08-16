"""Backward-compatible import path for :mod:`api.routers.practice_routes`."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("api.routers.practice_routes")

