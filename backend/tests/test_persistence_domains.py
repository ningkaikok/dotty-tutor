"""Contract tests for the focused persistence modules.

These tests intentionally instantiate each domain store directly. The app store
composition is covered separately so callers can depend on a narrow store.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import inspect

from persistence.mistake_store import MistakeStore
from persistence.review_store import ReviewStore
from persistence.learning_store import LearningStore
from persistence.textbook_store import TextbookStore
from persistence.app_store import AppStore
from persistence.tutoring_store import TutoringStore
from persistence.variation_store import VariationStore


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

    def test_app_store_combines_domains_with_one_engine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = AppStore(self.make_url(root, "combined.sqlite3"), root)
            try:
                self.assertTrue(callable(store.save_job))
                self.assertTrue(callable(store.save_lesson))
                self.assertTrue(store.ping())
            finally:
                store.close()

    def test_empty_database_creates_every_current_domain_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = AppStore(self.make_url(root, "current.sqlite3"), root)
            try:
                self.assertTrue(store.ping())
                MistakeStore(engine=store.engine, data_root=root).list("local-demo")
                TutoringStore(engine=store.engine).find_for_mistake("missing", "local-demo")
                VariationStore(engine=store.engine).count_for_mistake("missing")
                ReviewStore(engine=store.engine).list_for_mistake("missing")

                self.assertEqual(
                    set(inspect(store.engine).get_table_names()),
                    {
                        "background_jobs",
                        "batch_questions",
                        "exercise_attempts",
                        "learning_sessions",
                        "lesson_documents",
                        "lesson_publications",
                        "mastery_states",
                        "mistake_items",
                        "question_revisions",
                        "review_tasks",
                        "run_snapshots",
                        "tutor_messages",
                        "tutor_threads",
                        "upload_jobs",
                        "variation_exercises",
                    },
                )
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
