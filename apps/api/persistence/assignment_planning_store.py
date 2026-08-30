"""Persistence for teacher assignment planning drafts."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update

from persistence.database import decode_json
from persistence.schema import (
    assignment_plans,
    knowledge_points,
    mastery_states,
    metadata,
)


class AssignmentPlanningStore:
    """Keep planning drafts separate from classroom CRUD and assignment writes."""

    def __init__(self, *, engine: Any) -> None:
        self.engine = engine
        self._initialized = False

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            metadata.create_all(self.engine)
            self._initialized = True

    def create(
        self,
        *,
        plan_id: str,
        class_id: str,
        publication_id: str,
        publication_version: int,
        source_fingerprint: str,
        input_snapshot: dict[str, Any],
        result: dict[str, Any],
        warnings: list[dict[str, Any]],
        run_id: str | None,
        created_at: float,
    ) -> dict[str, Any]:
        self._ensure_initialized()
        with self.engine.begin() as connection:
            connection.execute(assignment_plans.insert().values(
                plan_id=plan_id,
                class_id=class_id,
                publication_id=publication_id,
                publication_version=publication_version,
                source_fingerprint=source_fingerprint,
                status="draft",
                input_snapshot_json=input_snapshot,
                result_json=result,
                warnings_json=warnings,
                run_id=run_id,
                assignment_id=None,
                created_at=created_at,
                updated_at=created_at,
                confirmed_at=None,
            ))
        return self.get(plan_id)  # type: ignore[return-value]

    def get(self, plan_id: str) -> dict[str, Any] | None:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            row = connection.execute(select(assignment_plans).where(assignment_plans.c.plan_id == plan_id)).mappings().first()
        return self._serialize(row) if row else None

    def update_result(self, plan_id: str, result: dict[str, Any], *, updated_at: float) -> None:
        self._ensure_initialized()
        with self.engine.begin() as connection:
            connection.execute(
                update(assignment_plans).where(assignment_plans.c.plan_id == plan_id).values(
                    result_json=result, updated_at=updated_at,
                )
            )

    def create_personalized_plan(
        self, *, plan_id: str, class_id: str, publication_id: str,
        source_fingerprint: str, input_snapshot: dict[str, Any], result: dict[str, Any],
        run_id: str | None, created_at: float,
    ) -> dict[str, Any]:
        """Persist the final generated paper as its own confirmable plan."""
        self._ensure_initialized()
        with self.engine.begin() as connection:
            connection.execute(assignment_plans.insert().values(
                plan_id=plan_id, class_id=class_id, publication_id=publication_id,
                publication_version=1, source_fingerprint=source_fingerprint,
                status="draft", input_snapshot_json=input_snapshot,
                result_json=result, warnings_json=[], run_id=run_id,
                assignment_id=None, created_at=created_at, updated_at=created_at,
                confirmed_at=None,
            ))
        return self.get(plan_id)  # type: ignore[return-value]

    def list_mastery_states(self, learner_ids: list[str]) -> list[dict[str, Any]]:
        """Join publication-scoped IDs to their publication/name for planning only."""
        self._ensure_initialized()
        if not learner_ids:
            return []
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(
                    mastery_states,
                    knowledge_points.c.publication_id,
                    knowledge_points.c.name,
                    knowledge_points.c.normalized_name,
                ).select_from(mastery_states.join(
                    knowledge_points,
                    mastery_states.c.knowledge_point_id == knowledge_points.c.knowledge_point_id,
                )).where(mastery_states.c.learner_id.in_(learner_ids))
            ).mappings().all()
        return [{
            "learnerId": row["learner_id"],
            "publicationId": row["publication_id"],
            "knowledgePointId": row["knowledge_point_id"],
            "name": row["name"],
            "normalizedName": row["normalized_name"],
            "score": row["score"],
            "evidenceCount": row["evidence_count"],
        } for row in rows]

    def confirm_and_create_assignment(
        self,
        *,
        plan_id: str,
        class_id: str,
        publication_id: str,
        title: str,
        due_at: float | None,
        source_fingerprint: str,
        warning_confirmed: bool,
        assignment_id: str,
        created_at: float,
    ) -> dict[str, Any]:
        """Atomically confirm a draft and create exactly one assignment."""
        self._ensure_initialized()
        from persistence.schema import assignments, lesson_publications

        with self.engine.begin() as connection:
            plan = connection.execute(
                select(assignment_plans).where(
                    assignment_plans.c.plan_id == plan_id,
                    assignment_plans.c.class_id == class_id,
                ).with_for_update()
            ).mappings().first()
            if not plan:
                raise LookupError("作业计划不存在或不属于当前班级")
            if plan["publication_id"] != publication_id:
                raise ValueError("作业计划与试卷不匹配")
            if plan["source_fingerprint"] != source_fingerprint:
                raise ValueError("作业计划已失效，请重新分析")
            if plan["status"] == "confirmed" and plan["assignment_id"]:
                existing = connection.execute(
                    select(assignments, lesson_publications.c.title.label("publication_title"), lesson_publications.c.lesson_ids_json)
                    .select_from(assignments.join(lesson_publications, assignments.c.publication_id == lesson_publications.c.publication_id))
                    .where(assignments.c.assignment_id == plan["assignment_id"])
                ).mappings().first()
                if existing:
                    return self._assignment_from_row(existing)
                raise ValueError("作业计划已确认但正式作业记录缺失")
            warnings = decode_json(plan["warnings_json"]) or []
            if warnings and not warning_confirmed:
                raise ValueError("请确认计划中的提醒后再布置作业")
            publication = connection.execute(
                select(lesson_publications).where(lesson_publications.c.publication_id == publication_id)
            ).mappings().first()
            if not publication or publication["status"] != "published":
                raise LookupError("只能指派已发布互动试卷")
            connection.execute(assignments.insert().values(
                assignment_id=assignment_id,
                class_id=class_id,
                publication_id=publication_id,
                assignment_plan_id=plan_id,
                title=title or publication["title"],
                due_at=due_at,
                status="active",
                created_at=created_at,
                updated_at=created_at,
            ))
            connection.execute(
                update(assignment_plans).where(assignment_plans.c.plan_id == plan_id).values(
                    status="confirmed", assignment_id=assignment_id,
                    updated_at=created_at, confirmed_at=created_at,
                )
            )
            row = connection.execute(
                select(assignments, lesson_publications.c.title.label("publication_title"), lesson_publications.c.lesson_ids_json)
                .select_from(assignments.join(lesson_publications, assignments.c.publication_id == lesson_publications.c.publication_id))
                .where(assignments.c.assignment_id == assignment_id)
            ).mappings().first()
        return self._assignment_from_row(row)  # type: ignore[arg-type]

    @staticmethod
    def _serialize(row: Any) -> dict[str, Any]:
        return {
            "planId": row["plan_id"],
            "classId": row["class_id"],
            "publicationId": row["publication_id"],
            "publicationVersion": row["publication_version"],
            "sourceFingerprint": row["source_fingerprint"],
            "status": row["status"],
            "inputSnapshot": decode_json(row["input_snapshot_json"]),
            "result": decode_json(row["result_json"]),
            "warnings": decode_json(row["warnings_json"]) or [],
            "runId": row["run_id"],
            "assignmentId": row["assignment_id"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "confirmedAt": row["confirmed_at"],
        }

    @staticmethod
    def _assignment_from_row(row: Any) -> dict[str, Any]:
        lesson_ids = decode_json(row.get("lesson_ids_json")) or []
        return {
            "assignmentId": row["assignment_id"],
            "classId": row["class_id"],
            "publicationId": row["publication_id"],
            "assignmentPlanId": row.get("assignment_plan_id"),
            "title": row["title"],
            "publicationTitle": row.get("publication_title") or row["title"],
            "className": row.get("class_name"),
            "dueAt": row["due_at"],
            "status": row["status"],
            "lessonIds": lesson_ids,
            "questionCount": len(lesson_ids),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
