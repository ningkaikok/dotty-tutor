from __future__ import annotations

# The test imports the repository-level executable to exercise its public
# contract; the path bootstrap intentionally precedes that import.
# ruff: noqa: E402
import json
import sys
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.seed_demo_bundle import load_manifest, mistake_id

from tests.postgres_test_support import PostgresTestCase, postgres_tests_enabled


class DemoBundleContractTests(unittest.TestCase):
    def test_manifest_is_synthetic_and_mistake_ids_are_stable(self) -> None:
        manifest_path = REPOSITORY_ROOT / "examples" / "demo-pack" / "manifest.json"
        manifest = load_manifest(manifest_path)
        self.assertEqual(manifest["sourceKind"], "synthetic-fixture")
        self.assertFalse(manifest["privacy"]["containsStudentData"])
        self.assertEqual(len(manifest["class"]["learners"]), 3)
        non_correct = [item for item in manifest["attempts"] if item["assessment"] != "correct"]
        expected = [
            mistake_id(item["learnerId"], manifest["publication"]["publicationId"], item["questionId"])
            for item in non_correct
        ]
        self.assertEqual(manifest["mistakeIds"], expected)
        self.assertEqual(
            json.loads(manifest_path.read_text(encoding="utf-8"))["mistakeIds"],
            expected,
        )

    def test_manifest_contains_no_network_or_model_requirement(self) -> None:
        manifest = load_manifest()
        self.assertEqual(manifest["privacy"]["containsReal教材"], False)
        self.assertEqual(manifest["review"]["intervalDays"], [1, 3])


@unittest.skipUnless(
    postgres_tests_enabled(),
    "需要 DOTTY_TEST_POSTGRES_ADMIN_URL 才运行 demo bundle PostgreSQL 集成测试",
)
class DemoBundleIntegrationTests(PostgresTestCase):
    def test_seed_is_idempotent_and_populates_teacher_views(self) -> None:
        from scripts.seed_demo_bundle import seed_bundle

        first = seed_bundle(self.database_url, data_root=self.data_root)
        second = seed_bundle(self.database_url, data_root=self.data_root)
        self.assertEqual(first, second)
        self.assertEqual(first["verified"], True)
        self.assertGreaterEqual(first["commonMistakeCount"], 1)
        self.assertGreater(first["evidenceRefCount"], 0)
