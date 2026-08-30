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

from sqlalchemy import and_, func, inspect, select
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
    from persistence.schema import exercise_attempts, learning_sessions
    from persistence.tutoring_store import tutor_threads
    from persistence.variation_store import variation_attempts, variation_exercises

    tables = set(inspect(engine).get_table_names())
    has_variation_attempts = "variation_attempts" in tables
    has_learning_attempts = {"exercise_attempts", "learning_sessions"} <= tables
    has_mistakes = "mistake_items" in tables
    has_reviews = "review_tasks" in tables
    has_tutoring = "tutor_threads" in tables
    with engine.connect() as connection:
        if has_mistakes:
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
        else:
            imported = 0
            confirmed = 0
        threads_started = connection.execute(
            select(func.count(func.distinct(tutor_threads.c.mistake_id))).where(
                tutor_threads.c.learner_id == learner_id
            )
        ).scalar_one() if has_tutoring else 0
        has_variation_exercises = "variation_exercises" in tables
        if has_variation_attempts:
            answered_variations = connection.execute(
                select(func.count()).select_from(variation_attempts).where(
                    variation_attempts.c.learner_id == learner_id
                )
            ).scalar_one()
            correct_variations = connection.execute(
                select(func.count()).select_from(variation_attempts).where(
                    and_(
                        variation_attempts.c.learner_id == learner_id,
                        variation_attempts.c.assessment == "correct",
                    )
                )
            ).scalar_one()
            # A deployed database can have the append-only table while still
            # containing historical projection rows written before that table
            # existed.  Count those legacy rows only when no attempt exists for
            # the same variation, so a migrated variation is never double-counted.
            if has_variation_exercises:
                no_attempt = ~select(variation_attempts.c.attempt_id).where(
                    variation_attempts.c.variation_id == variation_exercises.c.variation_id
                ).exists()
                legacy_answered = and_(
                    variation_exercises.c.learner_id == learner_id,
                    variation_exercises.c.status == "answered",
                    no_attempt,
                )
                answered_variations += connection.execute(
                    select(func.count()).select_from(variation_exercises).where(legacy_answered)
                ).scalar_one()
                legacy_correct = and_(legacy_answered, variation_exercises.c.assessment == "correct")
                correct_variations += connection.execute(
                    select(func.count()).select_from(variation_exercises).where(legacy_correct)
                ).scalar_one()
        elif has_variation_exercises:
            # Existing local databases may predate variation_attempts.  Use the
            # latest projection as a compatibility fallback until new answers
            # populate the append-only table; never rewrite that history here.
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
        else:
            answered_variations = 0
            correct_variations = 0
        reerror_rows = []
        if has_learning_attempts:
            reerror_rows = connection.execute(
                select(
                    exercise_attempts.c.publication_id,
                    exercise_attempts.c.knowledge_point_id,
                    exercise_attempts.c.knowledge_point,
                    exercise_attempts.c.assessment,
                    exercise_attempts.c.created_at,
                ).select_from(
                    exercise_attempts.join(
                        learning_sessions,
                        exercise_attempts.c.session_id == learning_sessions.c.session_id,
                    )
                ).where(
                    learning_sessions.c.learner_id == learner_id
                ).order_by(exercise_attempts.c.created_at)
            ).mappings().all()
        if has_reviews:
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
        else:
            scheduled_reviews = 0
            completed_reviews = 0

    contexts: dict[tuple[str, str], list[Any]] = {}
    for row in reerror_rows:
        context_key = (
            str(row["publication_id"]),
            str(row.get("knowledge_point_id") or row.get("knowledge_point") or "未分类"),
        )
        contexts.setdefault(context_key, []).append(row)
    reerror_denominator = 0
    reerror_numerator = 0
    for rows in contexts.values():
        first_error = next((index for index, row in enumerate(rows) if row["assessment"] != "correct"), None)
        if first_error is None or first_error >= len(rows) - 1:
            continue
        reerror_denominator += 1
        if any(row["assessment"] != "correct" for row in rows[first_error + 1:]):
            reerror_numerator += 1

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
        "learningEffect": {
            "sameKnowledgePointReerrorCount": reerror_numerator,
            "sameKnowledgePointReerrorDenominator": reerror_denominator,
            "sameKnowledgePointReerrorRate": _rate(reerror_numerator, reerror_denominator),
        },
    }
