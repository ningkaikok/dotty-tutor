from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from sqlalchemy import text

from tests.postgres_test_support import PostgresTestCase


def load_migration():
    path = Path(__file__).parents[2] / ".." / "scripts" / "migrate_variation_attribution.py"
    spec = importlib.util.spec_from_file_location("variation_attribution_migration", path.resolve())
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载迁移脚本")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VariationAttributionMigrationTests(PostgresTestCase):
    def test_legacy_table_dry_run_apply_and_verify(self) -> None:
        database = self.new_bare_database()
        with database.engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE variation_exercises (variation_id VARCHAR(64) PRIMARY KEY, strategy VARCHAR(32) NOT NULL)"
            ))
            connection.execute(text("INSERT INTO variation_exercises VALUES ('legacy-1', 'concept-foundation')"))
        migration = load_migration()
        self.assertFalse(migration.migrate(database.database_url)["column"])
        applied = migration.migrate(database.database_url, apply=True)
        self.assertTrue(applied["column"])
        verified = migration.verify(database.database_url)
        self.assertTrue(verified["verified"])
        with database.engine.connect() as connection:
            self.assertEqual(
                connection.execute(text(
                    "SELECT attribution_source FROM variation_exercises WHERE variation_id = 'legacy-1'"
                )).scalar_one(),
                "unknown",
            )


if __name__ == "__main__":
    unittest.main()
