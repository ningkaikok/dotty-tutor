"""Backward-compatible import path for :mod:`api.routers.tutoring_routes`."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("api.routers.tutoring_routes")

