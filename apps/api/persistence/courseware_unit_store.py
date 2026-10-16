"""Minimal persisted reading positions alongside existing courseware attempts."""

from typing import Any

from sqlalchemy import Column, Float, Integer, MetaData, String, Table, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from persistence.courseware_store import courseware_attempts

courseware_unit_metadata = MetaData()
courseware_reading_positions = Table(
    "courseware_reading_positions", courseware_unit_metadata,
    Column("learner_id", String(128), primary_key=True),
    Column("courseware_id", String(64), primary_key=True),
    Column("chapter_id", String(64), nullable=False, index=True),
    Column("publication_id", String(64), nullable=False),
    Column("page_id", String(128), nullable=False),
    Column("version", Integer, nullable=False),
    Column("command_id", String(128), nullable=False),
    Column("updated_at", Float, nullable=False),
)


class CoursewareUnitStore:
    """Use compare-and-swap so a stale device cannot overwrite newer navigation."""

    def __init__(self, *, engine: Engine) -> None:
        self.engine = engine

    @staticmethod
    def _position(row: Any) -> dict[str, Any]:
        return {"coursewareId": row["courseware_id"], "publicationId": row["publication_id"],
                "pageId": row["page_id"], "version": row["version"], "updatedAt": row["updated_at"]}

    def positions(self, learner_id: str, chapter_id: str) -> dict[str, dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(courseware_reading_positions).where(
                courseware_reading_positions.c.learner_id == learner_id,
                courseware_reading_positions.c.chapter_id == chapter_id,
            )).mappings().all()
        return {row["courseware_id"]: self._position(row) for row in rows}

    def attempts(self, learner_id: str, publication_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(
                courseware_attempts.c.activity_id, courseware_attempts.c.assessment,
            ).where(courseware_attempts.c.learner_id == learner_id,
                    courseware_attempts.c.publication_id == publication_id).order_by(
                        courseware_attempts.c.created_at, courseware_attempts.c.attempt_id,
                    )).mappings().all()
        return [{"activityId": row["activity_id"], "assessment": row["assessment"]} for row in rows]

    def save_position(self, *, learner_id: str, chapter_id: str, courseware_id: str,
                      publication_id: str, page_id: str, expected_version: int,
                      command_id: str, now: float) -> dict[str, Any]:
        table = courseware_reading_positions
        values = dict(learner_id=learner_id, chapter_id=chapter_id, courseware_id=courseware_id,
                      publication_id=publication_id, page_id=page_id, version=expected_version + 1,
                      command_id=command_id, updated_at=now)
        with self.engine.begin() as connection:
            statement = insert(table).values(**values)
            if expected_version == 0:
                statement = statement.on_conflict_do_nothing()
            else:
                # Updates must not create a row when the supplied version was never read.
                current = connection.execute(select(table).where(
                    table.c.learner_id == learner_id, table.c.courseware_id == courseware_id,
                ).with_for_update()).mappings().first()
                if current is None:
                    raise ValueError("学习位置已变化，请刷新单元目录后继续")
                statement = statement.on_conflict_do_update(
                    index_elements=[table.c.learner_id, table.c.courseware_id],
                    set_={key: value for key, value in values.items() if key not in {"learner_id", "courseware_id"}},
                    where=table.c.version == expected_version,
                )
            connection.execute(statement)
            row = connection.execute(select(table).where(
                table.c.learner_id == learner_id, table.c.courseware_id == courseware_id,
            )).mappings().one()
            if row["command_id"] != command_id or row["publication_id"] != publication_id or row["page_id"] != page_id:
                raise ValueError("学习位置已在其他页面更新，请刷新单元目录后继续")
            return self._position(row)
