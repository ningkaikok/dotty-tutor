from __future__ import annotations

import os
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from persistence.app_store import AppStore
from persistence.database import (
    DatabaseConfigurationError,
    build_postgres_url_from_env,
    resolve_database_url,
)


class StorageConfigTests(unittest.TestCase):
    def test_builds_password_url_and_encodes_credentials(self) -> None:
        values = {
            "POSTGRES_HOST": "db.internal",
            "POSTGRES_PORT": "5433",
            "POSTGRES_USER": "dotty@app",
            "POSTGRES_PASSWORD": "p@ss/word#1",
            "POSTGRES_DB": "dotty tutor",
            "POSTGRES_SSLMODE": "require",
        }
        with patch.dict(os.environ, values, clear=True):
            self.assertEqual(
                build_postgres_url_from_env(),
                "postgresql+psycopg://dotty%40app:p%40ss%2Fword%231@db.internal:5433/dotty%20tutor?sslmode=require",
            )

    def test_database_url_takes_precedence(self) -> None:
        with patch.dict(
            os.environ,
            {"DATABASE_URL": "postgres://app:secret@db.internal:5432/tutor"},
            clear=True,
        ):
            self.assertEqual(
                resolve_database_url(),
                "postgresql+psycopg://app:secret@db.internal:5432/tutor",
            )

    def test_rejects_sqlite_database_url_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {"DATABASE_URL": "sqlite+pysqlite:///:memory:"},
            clear=True,
        ):
            with self.assertRaisesRegex(DatabaseConfigurationError, "必须指向 PostgreSQL"):
                resolve_database_url()

    def test_rejects_non_postgres_database_url(self) -> None:
        with self.assertRaisesRegex(DatabaseConfigurationError, "必须指向 PostgreSQL"):
            resolve_database_url("mysql+pymysql://app:secret@db.internal/tutor")

    def test_requires_postgres_configuration(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(DatabaseConfigurationError, "未配置 PostgreSQL"):
                build_postgres_url_from_env()

    def test_data_directory_does_not_select_sqlite(self) -> None:
        with patch.dict(os.environ, {"DOTTY_DATA_DIR": "/tmp/dotty-test-data"}, clear=True):
            with self.assertRaisesRegex(DatabaseConfigurationError, "DOTTY_DATA_DIR"):
                resolve_database_url()

    def test_store_fails_fast_without_database_configuration(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(DatabaseConfigurationError, "未配置 PostgreSQL"):
                AppStore()

    def test_explicit_sqlite_url_remains_available_for_transition_tests(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            store = AppStore(database_url="sqlite+pysqlite:///:memory:", data_root=directory)
            try:
                self.assertEqual(store.backend, "sqlite")
            finally:
                store.close()
