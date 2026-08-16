"""Backward-compatible import path for :mod:`api.routers.library_routes`."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("api.routers.library_routes")

