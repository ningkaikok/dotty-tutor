"""Backward-compatible import path for audit contracts."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("domain.contracts.audit")

