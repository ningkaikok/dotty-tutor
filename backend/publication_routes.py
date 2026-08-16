"""Backward-compatible import path for :mod:`api.routers.publication_routes`."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("api.routers.publication_routes")

