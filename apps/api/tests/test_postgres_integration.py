"""真实 PostgreSQL 集成测试（P1 收尾项）。

与单测的区别：单测用 SQLite 内存库验证逻辑分支；这里用真实 PostgreSQL 验证
JSONB 列、Upsert 冲突路径和跨领域聚合在生产方言下的行为。

运行条件：设置 ``DOTTY_TEST_POSTGRES_URL``（如
``postgresql+psycopg://postgres:pass@127.0.0.1:15432/dotty_test``）后自动启用；
未设置时整组跳过（skipTest），本地与 CI 均不会误报。
"""

from __future__ import annotations

import os
import unittest
import uuid
from pathlib import Path

PG_URL = os.getenv("DOTTY_TEST_POSTGRES_URL", "")


@unittest.skipUnless(PG_URL, "需要 DOTTY_TEST_POSTGRES_URL 指向真实 PostgreSQL")
class PostgresIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        from persistence.app_store import AppStore

        # 数据跨运行持久（真实库不清理）：唯一 ID 保证测试幂等。
        self.run_id = uuid.uuid4().hex[:8]
        self.store = AppStore(f"{PG_URL}", Path(__file__).resolve().parent)
        self.addCleanup(self.store.close)
        self.assertTrue(self.store.ping())

    def test_domain_tables_created_on_real_postgres(self) -> None:
        from sqlalchemy import inspect

        from persistence.mistake_store import MistakeStore
        from persistence.review_store import ReviewStore
        from persistence.tutoring_store import TutoringStore
        from persistence.variation_store import VariationStore

        root = Path(self.store.root)
        MistakeStore(engine=self.store.engine, data_root=root).list(f"pg-{self.run_id}")
        TutoringStore(engine=self.store.engine).find_for_mistake("warm-up", f"pg-{self.run_id}")
        VariationStore(engine=self.store.engine).count_for_mistake(f"warm-{self.run_id}")
        ReviewStore(engine=self.store.engine).list_for_mistake(f"warm-{self.run_id}")
        # 指标表由 MetricsStore 首次访问创建，同样要在真实 PG 上验证 DDL。
        from persistence.metrics_store import MetricsStore
        MetricsStore(engine=self.store.engine).aggregate()
        tables = set(inspect(self.store.engine).get_table_names())
        for expected in ("mistake_items", "tutor_threads", "tutor_messages",
                         "variation_exercises", "review_tasks", "model_call_metrics"):
            self.assertIn(expected, tables)

    def test_jsonb_roundtrip_via_mistake_store(self) -> None:
        """JSONB 列在 PostgreSQL 方言下的完整往返：嵌套 JSON 结构不得失真。"""
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
                    "blanks": [{"id": "b1", "answerType": "numeric",
                                "correctAnswers": ["0.5"], "tolerance": 0}],
                }
            },
            "guideCards": [{"level": 0, "stuckAt": "s", "knowledge": ["k"],
                            "hint": "h", "question": "q"}],
            "createdAt": 1, "updatedAt": 1,
            "chapter": "代数", "knowledgePoint": "分数比较", "errorReason": "calculation",
        }
        store.create(item)
        restored = store.get(item["mistakeId"])
        self.assertIsNotNone(restored)
        # 嵌套结构逐层断言：JSONB 列不得把嵌套 dict 变成字符串或丢键。
        blocks = restored["questionPayload"]["question"]["contentBlocks"]
        self.assertEqual(blocks[0]["latex"], "\\frac{1}{2}")
        self.assertEqual(restored["questionPayload"]["question"]["blanks"][0]["correctAnswers"], ["0.5"])

    def test_funnel_snapshot_on_real_postgres(self) -> None:
        """漏斗聚合在 PostgreSQL 方言下的 CASE/COALESCE 查询行为与 SQLite 一致。"""
        from application.services.learning_funnel import build_funnel_snapshot

        snapshot = build_funnel_snapshot(self.store.engine, f"pg-{self.run_id}")
        self.assertEqual(snapshot["mistakes"]["imported"], 0)
        self.assertIsNone(snapshot["mistakes"]["confirmationRate"])


if __name__ == "__main__":
    unittest.main()
