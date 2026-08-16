"""Contract tests for the focused persistence modules.

These tests intentionally instantiate each domain store directly. Existing
application tests cover the compatibility ``TutorStore`` facade; this file
protects the new boundary so callers can depend on a narrow store.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from persistence.learning_store import LearningStore
from persistence.textbook_store import TextbookStore
from storage import TutorStore


class PersistenceDomainTests(unittest.TestCase):
    def make_url(self, root: Path, name: str) -> str:
        return f"sqlite+pysqlite:///{root / name}"

    def test_textbook_store_exposes_only_textbook_operations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TextbookStore(self.make_url(root, "textbook.sqlite3"), root)
            try:
                self.assertTrue(callable(store.save_job))
                self.assertFalse(hasattr(store, "record_exercise_attempt"))
            finally:
                store.close()

    def test_learning_store_exposes_only_learning_operations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LearningStore(self.make_url(root, "learning.sqlite3"), root)
            try:
                self.assertTrue(callable(store.save_lesson))
                self.assertFalse(hasattr(store, "save_job"))
            finally:
                store.close()

    def test_compatibility_store_combines_domains_with_one_engine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TutorStore(self.make_url(root, "combined.sqlite3"), root)
            try:
                self.assertTrue(callable(store.save_job))
                self.assertTrue(callable(store.save_lesson))
                self.assertTrue(store.ping())
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
