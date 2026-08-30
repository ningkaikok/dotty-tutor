"""Application persistence composition for the shared textbook and learning database."""

from persistence.classroom_store import ClassroomStore
from persistence.learning_store import LearningStore
from persistence.textbook_store import TextbookStore


class AppStore(TextbookStore, LearningStore, ClassroomStore):
    """Expose core application domains through one shared SQLAlchemy engine."""


application_store = AppStore()

__all__ = ["AppStore", "application_store"]
