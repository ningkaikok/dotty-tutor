"""Backward-compatible import path for the textbook application service."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("application.services.textbook_processing")

