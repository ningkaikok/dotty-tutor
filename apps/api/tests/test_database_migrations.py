"""SQLite acceptance tests for the single Alembic schema lifecycle."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Column, MetaData, Table, create_engine, inspect, text

from application import create_app
from persistence.migration_cli import alembic_config, current_revision
from persistence.migration_support import schema_report
from persistence.mistake_store import mistake_items
from persistence.review_store import review_tasks
from persistence.schema import assignments, learning_sessions
from persistence.schema_registry import SCHEMA_HEAD_REVISION, table_registry
from persistence.variation_store import variation_exercises
from routers.runtime_routes import build_runtime_router


class DatabaseMigrationTests(unittest.TestCase):
    def _url(self, directory: str, name: str = "database.sqlite3") -> str:
        return f"sqlite+pysqlite:///{Path(directory) / name}"

    def _create_legacy_projection(
        self,
        connection,
        source_table: Table,
        omitted_columns: set[str],
    ) -> None:
        """Create a legacy table with all known fields except the migration gap."""
        metadata = MetaData()
        Table(
            source_table.name,
            metadata,
            *(
                Column(
                    column.name,
                    column.type,
                    primary_key=column.primary_key,
                    nullable=column.nullable,
                )
                for column in source_table.columns
                if column.name not in omitted_columns
            ),
        ).create(connection)

    def test_registry_is_unique_and_alembic_upgrade_is_repeatable(self) -> None:
        self.assertEqual(len(table_registry()), 24)
        with tempfile.TemporaryDirectory() as directory:
            database_url = self._url(directory)
            command.upgrade(alembic_config(database_url), "head")
            engine = create_engine(database_url, future=True)
            first_tables = set(inspect(engine).get_table_names())
            with engine.connect() as connection:
                first_counts = {
                    table: connection.execute(text(f"SELECT COUNT(*) FROM \"{table}\"")).scalar_one()
                    for table in ("mistake_items", "mistake_attributions", "mastery_states")
                }
            command.upgrade(alembic_config(database_url), "head")
            self.assertEqual(current_revision(database_url), SCHEMA_HEAD_REVISION)
            self.assertIn(
                "fk_learning_sessions_assignment",
                {item["name"] for item in inspect(engine).get_foreign_keys("learning_sessions")},
            )
            self.assertIn(
                "fk_assignments_assignment_plan",
                {item["name"] for item in inspect(engine).get_foreign_keys("assignments")},
            )
            self.assertEqual(set(inspect(engine).get_table_names()), first_tables)
            with engine.connect() as connection:
                second_counts = {
                    table: connection.execute(text(f"SELECT COUNT(*) FROM \"{table}\"")).scalar_one()
                    for table in first_counts
                }
            self.assertEqual(second_counts, first_counts)
            engine.dispose()

    def test_partial_legacy_schema_is_adopted_and_legacy_data_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_url = self._url(directory, "partial.sqlite3")
            engine = create_engine(database_url, future=True)
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE lesson_publications (publication_id VARCHAR(64) PRIMARY KEY, title TEXT NOT NULL, source_upload_id TEXT, lesson_ids_json JSON NOT NULL, status VARCHAR(32) NOT NULL, version INTEGER NOT NULL, revision_of VARCHAR(64), created_at FLOAT NOT NULL, updated_at FLOAT NOT NULL)"
                )
                connection.exec_driver_sql(
                    "CREATE TABLE learning_sessions (session_id VARCHAR(64) PRIMARY KEY, learner_id VARCHAR(128) NOT NULL, publication_id VARCHAR(128) NOT NULL, started_at FLOAT NOT NULL, updated_at FLOAT NOT NULL)"
                )
                connection.exec_driver_sql(
                    "CREATE TABLE exercise_attempts (attempt_id VARCHAR(64) PRIMARY KEY, session_id VARCHAR(64) NOT NULL, question_id VARCHAR(128) NOT NULL, knowledge_point VARCHAR(160), assessment VARCHAR(32) NOT NULL, created_at FLOAT NOT NULL)"
                )
                connection.exec_driver_sql(
                    "CREATE TABLE mastery_states (learner_id VARCHAR(128) NOT NULL, knowledge_point VARCHAR(160) NOT NULL, score FLOAT NOT NULL, attempt_count INTEGER NOT NULL, correct_count INTEGER NOT NULL, last_practiced_at FLOAT, PRIMARY KEY (learner_id, knowledge_point))"
                )
                connection.exec_driver_sql(
                    "CREATE TABLE mistake_items (mistake_id VARCHAR(64) PRIMARY KEY, learner_id VARCHAR(128) NOT NULL, source_filename TEXT NOT NULL, content_type VARCHAR(255) NOT NULL, source_image_path TEXT NOT NULL, source_image_url TEXT NOT NULL, question_payload_json JSON NOT NULL, guide_cards_json JSON NOT NULL, ocr_run_json JSON NOT NULL, model_run_json JSON NOT NULL, original_answer TEXT NOT NULL, subject VARCHAR(80) NOT NULL, grade_band VARCHAR(80) NOT NULL, chapter TEXT NOT NULL, knowledge_point TEXT NOT NULL, error_reason VARCHAR(32), notes TEXT NOT NULL, status VARCHAR(32) NOT NULL, created_at FLOAT NOT NULL, updated_at FLOAT NOT NULL, confirmed_at FLOAT, ai_error_reason VARCHAR(32), ai_error_reason_confidence FLOAT)"
                )
                connection.exec_driver_sql(
                    "CREATE TABLE variation_exercises (variation_id VARCHAR(64) PRIMARY KEY, strategy VARCHAR(32) NOT NULL)"
                )
                connection.exec_driver_sql("INSERT INTO lesson_publications VALUES ('paper','paper',NULL,'[]','published',1,NULL,1,1)")
                connection.exec_driver_sql("INSERT INTO learning_sessions VALUES ('session','learner','paper',1,1)")
                connection.exec_driver_sql("INSERT INTO exercise_attempts VALUES ('attempt','session','question','一次函数','correct',2)")
                connection.exec_driver_sql("INSERT INTO mastery_states VALUES ('learner','一次函数',0.2,1,0,1)")
                connection.exec_driver_sql(
                    "INSERT INTO mistake_items VALUES ('mistake','learner','x','text','','','{}','[]','{}','{}','','数学','初中','章节','知识点','concept','','unmastered',1,1,NULL,'reading',0.8)"
                )
                connection.exec_driver_sql("INSERT INTO variation_exercises VALUES ('variation','concept')")

            command.upgrade(alembic_config(database_url), "head")
            with engine.connect() as connection:
                self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM mastery_states_legacy")).scalar_one(), 1)
                self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM mastery_states")).scalar_one(), 1)
                self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM mistake_attributions")).scalar_one(), 2)
                self.assertEqual(
                    connection.execute(text("SELECT source, category, confidence FROM mistake_attributions ORDER BY source")).all(),
                    [("ai", "reading", 0.8), ("self", "concept", 1.0)],
                )
                self.assertEqual(connection.execute(text("SELECT attribution_source FROM variation_exercises")).scalar_one(), "unknown")
                self.assertEqual(connection.execute(text("SELECT publication_id FROM exercise_attempts")).scalar_one(), "paper")

            command.upgrade(alembic_config(database_url), "head")
            with engine.connect() as connection:
                self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM mistake_attributions")).scalar_one(), 2)
            report = schema_report(engine)
            self.assertIn("fk_learning_sessions_assignment", report["missingForeignKeys"])
            self.assertEqual(report["orphanCounts"]["fk_learning_sessions_assignment"], 0)
            self.assertFalse(report["ready"])
            engine.dispose()

    def test_current_v027_schema_without_version_is_adopted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_url = self._url(directory, "current.sqlite3")
            engine = create_engine(database_url, future=True)
            from persistence.schema_registry import initialize_sqlite_schema

            initialize_sqlite_schema(engine)
            self.assertNotIn("alembic_version", inspect(engine).get_table_names())
            command.upgrade(alembic_config(database_url), "head")
            self.assertEqual(current_revision(database_url), SCHEMA_HEAD_REVISION)
            self.assertNotIn("mastery_states_legacy", inspect(engine).get_table_names())
            engine.dispose()

    def test_realistic_legacy_gap_shape_is_completed_idempotently(self) -> None:
        """Cover the exact partial PostgreSQL shape reported by read-only preflight."""
        with tempfile.TemporaryDirectory() as directory:
            database_url = self._url(directory, "realistic-gap.sqlite3")
            engine = create_engine(database_url, future=True)
            with engine.begin() as connection:
                self._create_legacy_projection(connection, assignments, {"assignment_plan_id"})
                self._create_legacy_projection(connection, learning_sessions, {"assignment_id"})
                self._create_legacy_projection(
                    connection,
                    mistake_items,
                    {"ai_error_reason", "ai_error_reason_confidence"},
                )
                self._create_legacy_projection(connection, review_tasks, {"evaluation_evidence_json"})
                self._create_legacy_projection(connection, variation_exercises, {"attribution_source"})
                connection.exec_driver_sql(
                    "INSERT INTO review_tasks "
                    "(task_id, mistake_id, learner_id, interval_days, due_at, status, "
                    "question_payload_json, model_run_json, response_json, assessment, feedback, "
                    "created_at, started_at, completed_at) "
                    "VALUES ('task', 'mistake', 'learner', 1, 1, 'scheduled', NULL, '{}', '{}', NULL, '', 1, NULL, NULL)"
                )

            preflight = schema_report(engine)
            self.assertIn("review_tasks.evaluation_evidence_json", preflight["autoFixable"]["columns"])
            command.upgrade(alembic_config(database_url), "head")
            command.upgrade(alembic_config(database_url), "head")
            with engine.connect() as connection:
                columns = {
                    table_name: {
                        column["name"] for column in inspect(connection).get_columns(table_name)
                    }
                    for table_name in (
                        "assignments",
                        "learning_sessions",
                        "mistake_items",
                        "review_tasks",
                        "variation_exercises",
                    )
                }
                self.assertIn("assignment_plan_id", columns["assignments"])
                self.assertIn("assignment_id", columns["learning_sessions"])
                self.assertIn("ai_error_reason", columns["mistake_items"])
                self.assertIn("ai_error_reason_confidence", columns["mistake_items"])
                self.assertIn("evaluation_evidence_json", columns["review_tasks"])
                self.assertIn("attribution_source", columns["variation_exercises"])
                self.assertEqual(
                    json.loads(
                        connection.execute(
                            text("SELECT evaluation_evidence_json FROM review_tasks WHERE task_id = 'task'")
                        ).scalar_one()
                    ),
                    {},
                )
                review_column = next(
                    column
                    for column in inspect(connection).get_columns("review_tasks")
                    if column["name"] == "evaluation_evidence_json"
                )
                self.assertFalse(review_column["nullable"])
                self.assertIn("mistake_attributions", inspect(connection).get_table_names())
                self.assertIn("variation_attempts", inspect(connection).get_table_names())
                self.assertIn("idx_assignments_plan", {
                    item["name"] for item in inspect(connection).get_indexes("assignments")
                })
                self.assertIn("idx_learning_sessions_assignment", {
                    item["name"] for item in inspect(connection).get_indexes("learning_sessions")
                })

            report = schema_report(engine)
            self.assertIn("fk_assignments_assignment_plan", report["manualActionRequired"]["foreignKeys"])
            self.assertIn("fk_learning_sessions_assignment", report["manualActionRequired"]["foreignKeys"])
            self.assertFalse(report["ready"])
            engine.dispose()

    def test_schema_report_distinguishes_unversioned_sqlite_and_missing_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_url = self._url(directory, "status.sqlite3")
            engine = create_engine(database_url, future=True)
            from persistence.schema_registry import initialize_sqlite_schema

            initialize_sqlite_schema(engine)
            unversioned = schema_report(engine)
            self.assertTrue(unversioned["ready"])
            self.assertEqual(unversioned["versionState"], "missing")
            with engine.begin() as connection:
                connection.exec_driver_sql("DROP TABLE mistake_attributions")
            missing = schema_report(engine)
            self.assertFalse(missing["ready"])
            self.assertIn("mistake_attributions", missing["missingTables"])
            engine.dispose()

    def test_health_reports_schema_out_of_date_before_business_queries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            from persistence.app_store import AppStore

            store = AppStore(self._url(directory, "health.sqlite3"), Path(directory))
            self.assertTrue(store.ping())
            with store.engine.begin() as connection:
                connection.exec_driver_sql("DROP TABLE mistake_attributions")
            app = create_app()
            app.include_router(
                build_runtime_router(
                    store=store,
                    question_payload=lambda: {},
                    tutor_runtime=object(),
                )
            )
            response = TestClient(app).get("/api/health")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["errorCode"], "SCHEMA_OUT_OF_DATE")
            self.assertIn("mistake_attributions", response.json()["details"]["missingTables"])
            self.assertIn("missingForeignKeys", response.json()["details"])
            self.assertIn("orphanCounts", response.json()["details"])
            self.assertIn("autoFixable", response.json()["details"])
            self.assertIn("manualActionRequired", response.json()["details"])
            store.close()


if __name__ == "__main__":
    unittest.main()
