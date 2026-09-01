"""Run the backend suite against one disposable PostgreSQL runtime database."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

from persistence.migration_cli import upgrade_database
from tests.postgres_test_support import PostgresTestDatabase


def main() -> int:
    with PostgresTestDatabase.create() as database:
        os.environ["DATABASE_URL"] = database.database_url
        with tempfile.TemporaryDirectory(prefix="dotty-pg-tests-") as data_root:
            os.environ["DOTTY_DATA_DIR"] = data_root
            upgrade_database(database.database_url)
            suite = unittest.defaultTestLoader.discover("tests", pattern="test_*.py")
            result = unittest.TextTestRunner(verbosity=1).run(suite)
            return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
