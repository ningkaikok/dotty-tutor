"""Backward-compatible import path for :mod:`api.routers.review_routes`."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("api.routers.review_routes")

