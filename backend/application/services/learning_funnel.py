"""学习效果漏斗聚合（roadmap T1#6 业务侧第一版）。

漏斗链路：错题导入 → 人工确认 → 开始陪练 → 变式验证通过 → 复习任务完成。

实现边界：
- 只聚合既有表，不新增事件埋点；成本/token 维度依赖"模型调用边界指标"
  落地后扩展到本模块。
- 跨域只读：直接以 SQLAlchemy Core 查询各领域表，不经过各 Store 的写路径，
  也不缓存——单机 Demo 数据量下毫秒级返回。
- 分母为零时比率为 ``None``（界面显示"暂无数据"），不用 0 冒充真实比率。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.engine import Engine

_CONFIRMED_STATUSES = ("unmastered", "mastered")


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 3)


def build_funnel_snapshot(engine: Engine, learner_id: str) -> dict[str, Any]:
    """聚合单个学习者的漏斗快照。只读，不修改任何状态。"""
    # 延迟导入避免持久化模块在 import 时产生循环依赖。
    from persistence.mistake_store import mistake_items
    from persistence.review_store import review_tasks
    from persistence.tutoring_store import tutor_threads
    from persistence.variation_store import variation_exercises

    with engine.connect() as connection:
        imported = connection.execute(
            select(func.count()).select_from(mistake_items).where(
                mistake_items.c.learner_id == learner_id
            )
        ).scalar_one()
        confirmed = connection.execute(
            select(func.count()).select_from(mistake_items).where(
                and_(
                    mistake_items.c.learner_id == learner_id,
                    mistake_items.c.status.in_(_CONFIRMED_STATUSES),
                )
            )
        ).scalar_one()
        threads_started = connection.execute(
            select(func.count(func.distinct(tutor_threads.c.mistake_id))).where(
                tutor_threads.c.learner_id == learner_id
            )
        ).scalar_one()
        answered_variations = connection.execute(
            select(func.count()).select_from(variation_exercises).where(
                and_(
                    variation_exercises.c.learner_id == learner_id,
                    variation_exercises.c.status == "answered",
                )
            )
        ).scalar_one()
        correct_variations = connection.execute(
            select(func.count()).select_from(variation_exercises).where(
                and_(
                    variation_exercises.c.learner_id == learner_id,
                    variation_exercises.c.status == "answered",
                    variation_exercises.c.assessment == "correct",
                )
            )
        ).scalar_one()
        scheduled_reviews = connection.execute(
            select(func.count()).select_from(review_tasks).where(
                review_tasks.c.learner_id == learner_id
            )
        ).scalar_one()
        completed_reviews = connection.execute(
            select(func.count()).select_from(review_tasks).where(
                and_(
                    review_tasks.c.learner_id == learner_id,
                    review_tasks.c.status == "completed",
                )
            )
        ).scalar_one()

    return {
        "learnerId": learner_id,
        "mistakes": {
            "imported": imported,
            "confirmed": confirmed,
            "confirmationRate": _rate(confirmed, imported),
        },
        "tutoring": {
            "confirmedMistakes": confirmed,
            "threadsStarted": threads_started,
        },
        "verification": {
            "answeredVariations": answered_variations,
            "correctVariations": correct_variations,
            "passRate": _rate(correct_variations, answered_variations),
        },
        "review": {
            "scheduledTasks": scheduled_reviews,
            "completedTasks": completed_reviews,
            "completionRate": _rate(completed_reviews, scheduled_reviews),
        },
    }
