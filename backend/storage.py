"""Backward-compatible persistence facade.

New code should depend on the narrow store that matches its domain:
``TextbookStore`` for imports and question batches, or ``LearningStore`` for
lessons and mastery.  ``TutorStore`` remains available for existing call sites,
tests and migration scripts while those dependencies are migrated gradually.
"""

from persistence.database import (
    DEFAULT_POSTGRES_URL,
    build_postgres_url_from_env,
    normalize_database_url,
)
from persistence.learning_store import LearningStore
from persistence.textbook_store import TextbookStore


class TutorStore(TextbookStore, LearningStore):
    """Compatibility store combining the two focused persistence domains.

    Both parents inherit :class:`DatabaseStore` and do not override its
    constructor, so Python's method-resolution order creates exactly one engine
    and one initialization lock for this facade.
    """


store = TutorStore()


__all__ = [
    "DEFAULT_POSTGRES_URL",
    "LearningStore",
    "TextbookStore",
    "TutorStore",
    "build_postgres_url_from_env",
    "normalize_database_url",
    "store",
]
