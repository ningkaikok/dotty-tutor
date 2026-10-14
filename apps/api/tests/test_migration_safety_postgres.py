"""Given/When/Then acceptance for lock-bounded, serialized and online schema migrations."""

from __future__ import annotations

import threading
import unittest
from typing import Any

from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import (
    Column,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    text,
)
from sqlalchemy.exc import OperationalError

from persistence.migration_cli import alembic_config, current_revision
from persistence.migration_support import (
    MIGRATION_LOCK_KEY,
    create_index_concurrently,
    migration_lock_timeout,
)
from persistence.schema_registry import SCHEMA_HEAD_REVISION
from tests.auth_test_support import environment
from tests.postgres_test_support import PostgresTestCase

PREVIOUS_REVISION = "0025_courseware_voice_context"


class MigrationSafetyTests(PostgresTestCase):
    def _upgrade_in_thread(self, url: str, target: str = "head") -> tuple[threading.Thread, dict[str, Any]]:
        outcome: dict[str, Any] = {}

        def run() -> None:
            try:
                command.upgrade(alembic_config(url), target)
                outcome["ok"] = True
            except Exception as error:  # surfaced to the asserting thread
                outcome["error"] = error

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread, outcome

    def test_user_deploys_while_a_long_transaction_holds_a_table_then_the_upgrade_fails_fast_and_rolls_back(self) -> None:
        # Given a database one revision behind head
        database = self.new_bare_database()
        command.upgrade(alembic_config(database.database_url), PREVIOUS_REVISION)
        # And another session holding a lock the upgrade needs (a long transaction on alembic_version)
        blocker = create_engine(database.url)
        self.addCleanup(blocker.dispose)
        connection = blocker.connect()
        self.addCleanup(connection.close)
        transaction = connection.begin()
        connection.execute(text("LOCK TABLE alembic_version IN ACCESS EXCLUSIVE MODE"))
        # When the deploy upgrades with a 500ms lock timeout
        with environment({"DOTTY_MIGRATION_LOCK_TIMEOUT": "500ms"}):
            thread, outcome = self._upgrade_in_thread(database.database_url)
            thread.join(timeout=30)
        # Then it gives up instead of queueing behind the long transaction, and nothing is half-applied
        self.assertFalse(thread.is_alive(), "upgrade kept waiting for the table lock")
        self.assertIsInstance(outcome.get("error"), OperationalError)
        transaction.rollback()
        # (Revisions build from the live table registry, so table presence cannot show a
        # half-applied upgrade; the recorded revision is what must not move.)
        self.assertEqual(current_revision(database.database_url), PREVIOUS_REVISION)
        # When the long transaction has ended, Then the same upgrade succeeds
        command.upgrade(alembic_config(database.database_url), "head")
        self.assertEqual(current_revision(database.database_url), SCHEMA_HEAD_REVISION)

    def test_user_starts_two_deploys_at_once_then_the_second_waits_for_the_first_instead_of_failing(self) -> None:
        # Given a database behind head and a first migrator holding the migration lock
        database = self.new_bare_database()
        command.upgrade(alembic_config(database.database_url), PREVIOUS_REVISION)
        holder = create_engine(database.url)
        self.addCleanup(holder.dispose)
        connection = holder.connect()
        self.addCleanup(connection.close)
        connection.execute(text(f"SELECT pg_advisory_lock({MIGRATION_LOCK_KEY})"))
        connection.commit()
        # When a second migrator starts with a short lock timeout
        with environment({"DOTTY_MIGRATION_LOCK_TIMEOUT": "200ms"}):
            thread, outcome = self._upgrade_in_thread(database.database_url)
            # Then it is still waiting well past its lock timeout (the timeout only bounds DDL)
            thread.join(timeout=3)
            self.assertTrue(thread.is_alive(), f"second migrator did not wait: {outcome}")
            # When the first migrator releases the lock, Then the second one completes the upgrade
            connection.execute(text(f"SELECT pg_advisory_unlock({MIGRATION_LOCK_KEY})"))
            connection.commit()
            thread.join(timeout=60)
        self.assertEqual(outcome, {"ok": True})
        self.assertEqual(current_revision(database.database_url), SCHEMA_HEAD_REVISION)

    def test_user_adds_an_index_to_a_populated_table_then_it_is_built_online_and_valid(self) -> None:
        # Given a table that already holds rows
        metadata = MetaData()
        table = Table("index_target", metadata, Column("id", Integer, primary_key=True), Column("label", String(32)))
        with self.engine.begin() as connection:
            metadata.create_all(connection)
            connection.execute(table.insert(), [{"id": number, "label": f"row-{number}"} for number in range(1, 201)])
        # When a migration step adds an index with the concurrent helper
        with self.engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                create_index_concurrently(Index("ix_index_target_label", table.c.label))
        # Then the index exists and PostgreSQL marks it valid (a failed concurrent build would not be)
        with self.engine.connect() as connection:
            valid = connection.execute(text(
                "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = 'ix_index_target_label'")).scalar_one()
        self.assertTrue(valid)


class MigrationLockTimeoutConfigTests(unittest.TestCase):
    def test_user_configures_the_lock_timeout_then_only_bounded_durations_are_accepted(self) -> None:
        with environment({"DOTTY_MIGRATION_LOCK_TIMEOUT": "750ms"}):
            self.assertEqual(migration_lock_timeout(), "750ms")
        for invalid in ("0s", "5 minutes", "5s; DROP TABLE x", "", "-1s"):
            with self.subTest(value=invalid), environment({"DOTTY_MIGRATION_LOCK_TIMEOUT": invalid}):
                with self.assertRaises(ValueError):
                    migration_lock_timeout()
