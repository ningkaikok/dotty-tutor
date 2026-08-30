from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, text


def load_migration():
    path = Path(__file__).parents[2] / ".." / "scripts" / "migrate_variation_attribution.py"
    spec = importlib.util.spec_from_file_location("variation_attribution_migration", path.resolve())
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载迁移脚本")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VariationAttributionMigrationTests(unittest.TestCase):
    def test_old_sqlite_table_dry_run_apply_and_verify(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_url = f"sqlite:///{Path(directory) / 'old.sqlite3'}"
            engine = create_engine(database_url, future=True)
            with engine.begin() as connection:
                connection.execute(text(
                    "CREATE TABLE variation_exercises (variation_id VARCHAR(64) PRIMARY KEY, strategy VARCHAR(32) NOT NULL)"
                ))
                connection.execute(text("INSERT INTO variation_exercises VALUES ('legacy-1', 'concept-foundation')"))
            migration = load_migration()
            self.assertFalse(migration.migrate(database_url)["column"])
            applied = migration.migrate(database_url, apply=True)
            self.assertTrue(applied["column"])
            verified = migration.verify(database_url)
            self.assertTrue(verified["verified"])
            with engine.connect() as connection:
                self.assertEqual(connection.execute(text(
                    "SELECT attribution_source FROM variation_exercises WHERE variation_id = 'legacy-1'"
                )).scalar_one(), "unknown")
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
