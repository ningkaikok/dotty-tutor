"""Application persistence composition for the shared textbook and learning database."""

from persistence.classroom_store import ClassroomStore
from persistence.learning_store import LearningStore
from persistence.textbook_store import TextbookStore


class AppStore(TextbookStore, LearningStore, ClassroomStore):
    """Expose core application domains through one shared SQLAlchemy engine."""


_application_store: AppStore | None = None


def get_application_store() -> AppStore:
    """Create the formal runtime store lazily, after configuration is loaded."""
    global _application_store
    if _application_store is None:
        _application_store = AppStore()
    return _application_store

__all__ = ["AppStore", "get_application_store"]
