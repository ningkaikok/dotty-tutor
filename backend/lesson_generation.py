"""Backward-compatible import path for the lesson application service."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("application.services.lesson_generation")

