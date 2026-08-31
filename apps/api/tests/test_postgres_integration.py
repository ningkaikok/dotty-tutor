"""真实 PostgreSQL 集成测试（只使用一次性隔离测试数据库）。"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
import uuid
from argparse import Namespace
from pathlib import Path

from sqlalchemy import inspect, text

from tests.postgres_test_support import PostgresTestDatabase, postgres_tests_enabled


@unittest.skipUnless(
    postgres_tests_enabled(),
    "需要 DOTTY_TEST_POSTGRES_ADMIN_URL 指向隔离 PostgreSQL admin 库",
)
class PostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database = PostgresTestDatabase.create()
        cls.addClassCleanup(cls.database.close)
        cls.data_root = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.data_root.cleanup)

        from persistence.migration_cli import upgrade_database

        upgrade_database(cls.database.database_url)

    def setUp(self) -> None:
        from persistence.app_store import AppStore

        self.run_id = uuid.uuid4().hex[:8]
        self.store = AppStore(self.database.database_url, Path(self.data_root.name))
        self.addCleanup(self.store.close)
        self.assertTrue(self.store.ping())

    def new_bare_database(self) -> PostgresTestDatabase:
        """Create a database with no application tables for legacy-shape tests."""
        database = PostgresTestDatabase.create()
        self.addCleanup(database.close)
        from persistence.migration_cli import current_revision

        self.assertEqual(inspect(database.engine).get_table_names(), [])
        self.assertIsNone(current_revision(database.database_url))
        return database

    def new_migrated_database(self) -> PostgresTestDatabase:
        """Create a fresh database, migrate it, and assert it is at schema head."""
        database = self.new_bare_database()
        from persistence.migration_cli import current_revision, upgrade_database
        from persistence.schema_registry import SCHEMA_HEAD_REVISION

        result = upgrade_database(database.database_url)
        self.assertEqual(result["current"], SCHEMA_HEAD_REVISION)
        self.assertEqual(current_revision(database.database_url), SCHEMA_HEAD_REVISION)
        return database

    def test_domain_tables_exist_on_real_postgres(self) -> None:
        from persistence.metrics_store import MetricsStore
        from persistence.mistake_store import MistakeStore
        from persistence.review_store import ReviewStore
        from persistence.tutoring_store import TutoringStore
        from persistence.variation_store import VariationStore

        root = Path(self.store.root)
        MistakeStore(engine=self.store.engine, data_root=root).list(f"pg-{self.run_id}")
        TutoringStore(engine=self.store.engine).find_for_mistake("warm-up", f"pg-{self.run_id}")
        VariationStore(engine=self.store.engine).count_for_mistake(f"warm-{self.run_id}")
        ReviewStore(engine=self.store.engine).list_for_mistake(f"warm-{self.run_id}")
        MetricsStore(engine=self.store.engine).aggregate()
        tables = set(inspect(self.store.engine).get_table_names())
        for expected in (
            "mistake_items",
            "tutor_threads",
            "tutor_messages",
            "variation_exercises",
            "review_tasks",
            "model_call_metrics",
        ):
            self.assertIn(expected, tables)

    def test_jsonb_roundtrip_and_review_default_on_real_postgres(self) -> None:
        """JSONB preserves nested evidence and additive defaults preserve old rows."""
        from persistence.mistake_store import MistakeStore

        store = MistakeStore(engine=self.store.engine, data_root=Path(self.store.root))
        item = {
            "mistakeId": f"pg-{self.run_id}-1",
            "learnerId": f"pg-{self.run_id}",
            "sourceFilename": "x.jpg",
            "contentType": "image/jpeg",
            "sourceImagePath": "/data/x.jpg",
            "sourceImageUrl": f"/api/mistakes/{self.run_id}-1/source",
            "questionPayload": {
                "question": {
                    "id": "q-pg",
                    "questionType": "fill-blank",
                    "prompt": "求 $\\frac{1}{2}$ 与 $\\sqrt{2}$ 的大小。",
                    "contentBlocks": [
                        {"type": "math", "id": "m1", "latex": "\\frac{1}{2}"},
                        {"type": "text", "id": "t1", "text": "比较大小"},
                    ],
                    "options": [],
                    "givens": [],
                    "blanks": [{"id": "b1", "answerType": "numeric", "correctAnswers": ["0.5"], "tolerance": 0}],
                }
            },
            "guideCards": [{"level": 0, "stuckAt": "s", "knowledge": ["k"], "hint": "h", "question": "q"}],
            "createdAt": 1,
            "updatedAt": 1,
            "chapter": "代数",
            "knowledgePoint": "分数比较",
            "errorReason": "calculation",
        }
        store.create(item)
        restored = store.get(item["mistakeId"])
        self.assertIsNotNone(restored)
        self.assertEqual(
            restored["questionPayload"]["question"]["contentBlocks"][0]["latex"],
            "\\frac{1}{2}",
        )
        self.assertEqual(
            restored["questionPayload"]["question"]["blanks"][0]["correctAnswers"],
            ["0.5"],
        )

        database = self.new_bare_database()
        from persistence.migration_cli import upgrade_database
        from persistence.migration_support import create_registered_schema

        with database.engine.begin() as connection:
            create_registered_schema(connection)
            connection.execute(text('ALTER TABLE "review_tasks" DROP COLUMN "evaluation_evidence_json"'))
        upgrade_database(database.database_url)
        with database.engine.connect() as connection:
            review_column = next(
                column
                for column in inspect(connection).get_columns("review_tasks")
                if column["name"] == "evaluation_evidence_json"
            )
            self.assertFalse(review_column["nullable"])
            self.assertIn("jsonb", str(review_column["type"]).lower())
            task_id = f"task-{self.run_id}"
            connection.execute(
                text(
                    "INSERT INTO review_tasks "
                    "(task_id, mistake_id, learner_id, interval_days, due_at, status, model_run_json, response_json, feedback, created_at) "
                    "VALUES (:task_id, :mistake_id, :learner_id, 1, 1, 'scheduled', '{}'::jsonb, '{}'::jsonb, '', 1)"
                ),
                {
                    "task_id": task_id,
                    "mistake_id": f"mistake-{self.run_id}",
                    "learner_id": f"learner-{self.run_id}",
                },
            )
            connection.commit()
            evidence = connection.execute(
                text("SELECT evaluation_evidence_json FROM review_tasks WHERE task_id = :task_id"),
                {"task_id": task_id},
            ).scalar_one()
        self.assertEqual(evidence, {})

    def test_migration_cli_and_adoption_are_idempotent(self) -> None:
        from persistence.migration_cli import (
            alembic_config,
            current_revision,
            head_revision,
            run,
            upgrade_database,
        )
        from persistence.schema_registry import SCHEMA_HEAD_REVISION

        config = alembic_config(self.database.database_url)
        self.assertEqual(head_revision(config), SCHEMA_HEAD_REVISION)
        self.assertEqual(current_revision(self.database.database_url), SCHEMA_HEAD_REVISION)
        code, preflight = run(
            Namespace(command="preflight", database_url=self.database.database_url, data_root=None)
        )
        self.assertEqual(code, 0)
        self.assertTrue(preflight["ready"])
        self.assertEqual(upgrade_database(self.database.database_url)["current"], SCHEMA_HEAD_REVISION)
        self.assertEqual(upgrade_database(self.database.database_url)["current"], SCHEMA_HEAD_REVISION)
        code, verified = run(
            Namespace(command="verify", database_url=self.database.database_url, data_root=None)
        )
        self.assertEqual(code, 0)
        self.assertTrue(verified["ready"])

        database = self.new_bare_database()
        from persistence.migration_support import create_registered_schema

        with database.engine.begin() as connection:
            create_registered_schema(connection, exclude_tables={"mistake_attributions"})
        self.assertIsNone(current_revision(database.database_url))
        upgrade_database(database.database_url)
        self.assertEqual(current_revision(database.database_url), SCHEMA_HEAD_REVISION)

    def test_preflight_classifies_auto_fixable_columns_and_indexes(self) -> None:
        from persistence.migration_cli import upgrade_database
        from persistence.migration_support import (
            create_registered_schema,
            schema_report,
        )

        database = self.new_bare_database()
        with database.engine.begin() as connection:
            create_registered_schema(connection)
            connection.execute(text('DROP INDEX "idx_assignments_plan"'))
            connection.execute(text('ALTER TABLE "assignments" DROP COLUMN "assignment_plan_id"'))
            connection.execute(text('DROP INDEX "idx_learning_sessions_assignment"'))
            connection.execute(text('ALTER TABLE "learning_sessions" DROP COLUMN "assignment_id"'))
        report = schema_report(database.engine)
        self.assertIn("assignments.assignment_plan_id", report["autoFixable"]["columns"])
        self.assertIn("idx_assignments_plan", report["autoFixable"]["indexes"])
        self.assertIn("idx_learning_sessions_assignment", report["autoFixable"]["indexes"])
        self.assertEqual(report["manualActionRequired"]["indexes"], [])
        upgrade_database(database.database_url)
        self.assertTrue(schema_report(database.engine)["ready"])

    def test_assignment_foreign_keys_establish_and_reject_orphans(self) -> None:
        from persistence.migration_support import (
            ensure_registered_foreign_keys,
            schema_report,
        )

        database = self.new_migrated_database()
        with database.engine.begin() as connection:
            connection.execute(text('ALTER TABLE "assignments" DROP CONSTRAINT "fk_assignments_assignment_plan"'))
            connection.execute(text('ALTER TABLE "learning_sessions" DROP CONSTRAINT "fk_learning_sessions_assignment"'))
            added = ensure_registered_foreign_keys(connection)
        self.assertEqual(
            set(added),
            {"fk_assignments_assignment_plan", "fk_learning_sessions_assignment"},
        )
        with database.engine.begin() as connection:
            connection.execute(text('ALTER TABLE "assignments" DROP CONSTRAINT "fk_assignments_assignment_plan"'))
            connection.execute(
                text(
                    "INSERT INTO learning_classes (class_id, name, subject, grade_band, created_at, updated_at) "
                    "VALUES ('class-orphan', '测试班', '数学', '初中', 1, 1)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO lesson_publications "
                    "(publication_id, title, lesson_ids_json, status, version, created_at, updated_at) "
                    "VALUES ('publication-orphan', '测试卷', '[]'::jsonb, 'published', 1, 1, 1)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO assignments "
                    "(assignment_id, class_id, publication_id, assignment_plan_id, title, status, created_at, updated_at) "
                    "VALUES ('assignment-orphan', 'class-orphan', 'publication-orphan', 'missing-plan', '测试作业', 'active', 1, 1)"
                )
            )

        with database.engine.begin() as connection:
            with self.assertRaisesRegex(RuntimeError, "孤儿引用"):
                ensure_registered_foreign_keys(connection)
        report = schema_report(database.engine)
        self.assertEqual(report["orphanCounts"]["fk_assignments_assignment_plan"], 1)

    def test_advisory_migration_lock_blocks_second_transaction(self) -> None:
        first = self.database.engine.connect()
        first_transaction = first.begin()
        self.addCleanup(first.close)
        first.execute(text("SELECT pg_advisory_xact_lock(734291507)"))
        started = threading.Event()
        acquired = threading.Event()
        errors: list[Exception] = []

        def wait_for_lock() -> None:
            try:
                with self.database.engine.connect() as second:
                    second_transaction = second.begin()
                    started.set()
                    second.execute(text("SELECT pg_advisory_xact_lock(734291507)"))
                    acquired.set()
                    second_transaction.rollback()
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=wait_for_lock)
        thread.start()
        self.assertTrue(started.wait(2))
        time.sleep(0.2)
        self.assertFalse(acquired.is_set())
        first_transaction.commit()
        first.close()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(acquired.is_set())

    def test_funnel_snapshot_on_real_postgres(self) -> None:
        from application.services.learning_funnel import build_funnel_snapshot

        snapshot = build_funnel_snapshot(self.store.engine, f"pg-{self.run_id}")
        self.assertEqual(snapshot["mistakes"]["imported"], 0)
        self.assertIsNone(snapshot["mistakes"]["confirmationRate"])


if __name__ == "__main__":
    unittest.main()
