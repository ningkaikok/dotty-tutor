"""Backward-compatible import path for the review persistence store."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("persistence.review_store")

