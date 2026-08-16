"""Backward-compatible import path for upload file coordination."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("infrastructure.files.upload_registry")

