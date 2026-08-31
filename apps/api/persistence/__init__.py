"""Focused persistence stores for the modular FastAPI application.

The package exports remain available through lazy attribute loading. Importing
``persistence.migration_cli`` or schema tooling must not construct the
application-wide default Store or emit database configuration warnings.
"""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from persistence.app_store import AppStore
    from persistence.base import DatabaseStore
    from persistence.job_store import JobStore
    from persistence.learning_store import LearningStore
    from persistence.mistake_store import MistakeStore
    from persistence.review_store import ReviewStore
    from persistence.textbook_store import TextbookStore
    from persistence.tutoring_store import TutoringStore
    from persistence.variation_store import VariationStore

_EXPORTS = {
    "DatabaseStore": ("persistence.base", "DatabaseStore"),
    "AppStore": ("persistence.app_store", "AppStore"),
    "JobStore": ("persistence.job_store", "JobStore"),
    "LearningStore": ("persistence.learning_store", "LearningStore"),
    "MistakeStore": ("persistence.mistake_store", "MistakeStore"),
    "ReviewStore": ("persistence.review_store", "ReviewStore"),
    "TextbookStore": ("persistence.textbook_store", "TextbookStore"),
    "TutoringStore": ("persistence.tutoring_store", "TutoringStore"),
    "VariationStore": ("persistence.variation_store", "VariationStore"),
}


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attribute = _EXPORTS[name]
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value

__all__ = [
    "DatabaseStore",
    "AppStore",
    "LearningStore",
    "JobStore",
    "MistakeStore",
    "ReviewStore",
    "TextbookStore",
    "TutoringStore",
    "VariationStore",
]
