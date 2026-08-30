"""学习漏斗聚合的验收测试（roadmap T1#6 业务侧第一版）。

用真实 SQLite 引擎播种错题/线程/变式/复习四个领域的数据，
验证漏斗数字与比率的计算，以及"分母为零时比率为 None"的约定。
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from application.services.learning_funnel import build_funnel_snapshot
from persistence.app_store import AppStore
from persistence.mistake_store import MistakeStore
from persistence.review_store import ReviewStore
from persistence.tutoring_store import TutoringStore
from persistence.variation_store import VariationStore

_CONFIRMATION = {
    "prompt": "（3分）修正后的题干",
    "chapter": "代数",
    "knowledgePoint": "一元一次方程",
    "errorReason": "calculation",
}


def _mistake_item(mistake_id: str) -> dict:
    return {
        "mistakeId": mistake_id,
        "learnerId": "local-demo",
        "sourceFilename": f"{mistake_id}.jpg",
        "contentType": "image/jpeg",
        "sourceImagePath": f"/data/{mistake_id}.jpg",
        "sourceImageUrl": f"/api/mistakes/{mistake_id}/source",
        "questionPayload": {
            "question": {
                "id": f"question-{mistake_id}",
                "questionType": "choice",
                "prompt": f"{mistake_id} 的原始题干",
                "contentBlocks": [],
                "options": [],
                "givens": [],
            }
        },
        "guideCards": [],
        "createdAt": time.time(),
        "updatedAt": time.time(),
        "chapter": "代数",
        "knowledgePoint": "一元一次方程",
        "errorReason": "calculation",
    }


class LearningFunnelTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        data_root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        store = AppStore(f"sqlite+pysqlite:///{data_root / 'funnel.sqlite3'}", data_root)
        self.addCleanup(store.close)
        self.engine = store.engine
        self.mistakes = MistakeStore(engine=self.engine, data_root=data_root)
        self.tutoring = TutoringStore(engine=self.engine)
        self.variations = VariationStore(engine=self.engine)
        self.reviews = ReviewStore(engine=self.engine)
        # 预热建表：各领域表由首次访问的 Store 创建，漏斗是只读聚合，
        # 不应承担建表职责。
        self.mistakes.list("local-demo")
        self.tutoring.find_for_mistake("warm-up", "local-demo")
        self.variations.count_for_mistake("warm-up")
        self.reviews.list_for_mistake("warm-up")

    def _seed_confirmed_mistake(self, mistake_id: str, *, mastered: bool = False) -> None:
        self.mistakes.create(_mistake_item(mistake_id))
        self.mistakes.confirm(mistake_id, dict(_CONFIRMATION))
        if mastered:
            self.mistakes.mark_mastered(mistake_id)

    def test_empty_learner_has_zero_counts_and_none_rates(self) -> None:
        snapshot = build_funnel_snapshot(self.engine, "nobody")
        self.assertEqual(snapshot["mistakes"]["imported"], 0)
        # 分母为零：比率必须是 None 而不是 0，界面据此显示"暂无数据"。
        self.assertIsNone(snapshot["mistakes"]["confirmationRate"])
        self.assertIsNone(snapshot["review"]["completionRate"])

    def test_funnel_counts_each_stage(self) -> None:
        # 三道导入：两道确认（其一已 mastered），一道仍待确认
        self._seed_confirmed_mistake("m-0", mastered=True)
        self._seed_confirmed_mistake("m-1")
        self.mistakes.create(_mistake_item("m-2"))

        snapshot = build_funnel_snapshot(self.engine, "local-demo")
        self.assertEqual(snapshot["mistakes"]["imported"], 3)
        self.assertEqual(snapshot["mistakes"]["confirmed"], 2)
        self.assertEqual(snapshot["mistakes"]["confirmationRate"], round(2 / 3, 3))

        # 两道确认题都开始陪练
        self.tutoring.create_or_get("m-0", "local-demo")
        self.tutoring.create_or_get("m-1", "local-demo")
        snapshot = build_funnel_snapshot(self.engine, "local-demo")
        self.assertEqual(snapshot["tutoring"]["threadsStarted"], 2)

        # m-0 完成变式验证且答对
        created_variation = self.variations.create(
            mistake_id="m-0", learner_id="local-demo",
            strategy="calculation", level="foundation",
            question_payload={"question": {"id": "v-1"}}, model_run={},
        )
        # variation_id 由 Store 生成；用错误的 ID 调用会静默返回 None，
        # 这本身就是漏斗数字能暴露的"静默失败"类别。
        self.variations.answer(
            created_variation["variationId"], response={"interactionResult": {}},
            attempt_id="variation-wrong",
            assessment="incorrect", feedback="",
        )
        self.variations.answer(
            created_variation["variationId"], response={"interactionResult": {}},
            attempt_id="variation-correct",
            assessment="correct", feedback="",
        )
        snapshot = build_funnel_snapshot(self.engine, "local-demo")
        self.assertEqual(snapshot["verification"]["answeredVariations"], 2)
        self.assertEqual(snapshot["verification"]["correctVariations"], 1)
        self.assertEqual(snapshot["verification"]["passRate"], 0.5)

        # 复习任务只排一个间隔并完成
        self.reviews.schedule(mistake_id="m-0", learner_id="local-demo", intervals=(1,))
        tasks = self.reviews.list_for_mistake("m-0")
        # 生命周期：scheduled → ready（开始）→ completed（作答）
        self.reviews.start(
            tasks[0]["taskId"],
            question_payload={"question": {"id": "r-1"}}, model_run={},
        )
        self.reviews.answer(
            tasks[0]["taskId"], response={},
            assessment="correct", feedback="",
        )
        snapshot = build_funnel_snapshot(self.engine, "local-demo")
        self.assertEqual(snapshot["review"]["scheduledTasks"], 1)
        self.assertEqual(snapshot["review"]["completedTasks"], 1)
        self.assertEqual(snapshot["review"]["completionRate"], 1.0)

    def test_legacy_answered_projection_is_added_only_without_attempt(self) -> None:
        from persistence.variation_store import variation_exercises

        legacy = self.variations.create(
            mistake_id="legacy", learner_id="local-demo", strategy="calculation",
            level="foundation", question_payload={"question": {"id": "legacy-q"}}, model_run={},
        )
        migrated = self.variations.create(
            mistake_id="migrated", learner_id="local-demo", strategy="calculation",
            level="foundation", question_payload={"question": {"id": "migrated-q"}}, model_run={},
        )
        with self.engine.begin() as connection:
            connection.execute(
                variation_exercises.update()
                .where(variation_exercises.c.variation_id == legacy["variationId"])
                .values(status="answered", assessment="correct", response_json={"legacy": True}, feedback=""),
            )
        self.variations.answer(
            migrated["variationId"], response={"interactionResult": {}},
            attempt_id="migrated-attempt", assessment="correct", feedback="",
        )
        snapshot = build_funnel_snapshot(self.engine, "local-demo")
        self.assertEqual(snapshot["verification"]["answeredVariations"], 2)
        self.assertEqual(snapshot["verification"]["correctVariations"], 2)


if __name__ == "__main__":
    unittest.main()
