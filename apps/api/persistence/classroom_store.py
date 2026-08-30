"""班级、作业指派和教师掌握度视图的持久化查询。"""

from __future__ import annotations

import time
from typing import Any, Sequence

from sqlalchemy import select

from domain.learning.mastery import knowledge_point_id, normalize_knowledge_point_name
from persistence.base import DatabaseStore
from persistence.database import decode_json
from persistence.schema import (
    assignments,
    class_memberships,
    exercise_attempts,
    learning_classes,
    learning_sessions,
    lesson_documents,
    lesson_publications,
)


class ClassroomStore(DatabaseStore):
    """保存教学编排数据，并从已有学习证据派生班级看板。"""

    def create_class(
        self,
        *,
        class_id: str,
        name: str,
        subject: str,
        grade_band: str,
        created_at: float,
    ) -> dict[str, Any]:
        self._ensure_initialized()
        with self.engine.begin() as connection:
            connection.execute(learning_classes.insert().values(
                class_id=class_id,
                name=name,
                subject=subject,
                grade_band=grade_band,
                created_at=created_at,
                updated_at=created_at,
            ))
        return self.get_class(class_id)  # type: ignore[return-value]

    def get_class(self, class_id: str) -> dict[str, Any] | None:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(learning_classes).where(learning_classes.c.class_id == class_id)
            ).mappings().first()
            if not row:
                return None
            members = connection.execute(
                select(class_memberships)
                .where(class_memberships.c.class_id == class_id)
                .order_by(class_memberships.c.display_name.asc(), class_memberships.c.learner_id.asc())
            ).mappings().all()
            assignment_rows = connection.execute(
                select(assignments, lesson_publications.c.title.label("publication_title"))
                .select_from(assignments.join(
                    lesson_publications,
                    assignments.c.publication_id == lesson_publications.c.publication_id,
                ))
                .where(assignments.c.class_id == class_id)
                .order_by(assignments.c.created_at.desc())
            ).mappings().all()
        return {
            "classId": row["class_id"],
            "name": row["name"],
            "subject": row["subject"],
            "gradeBand": row["grade_band"],
            "memberCount": len(members),
            "members": [{
                "learnerId": member["learner_id"],
                "displayName": member["display_name"],
                "joinedAt": member["joined_at"],
            } for member in members],
            "assignments": [self._assignment_from_row(item) for item in assignment_rows],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def list_classes(self) -> list[dict[str, Any]]:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(learning_classes).order_by(learning_classes.c.updated_at.desc())
            ).mappings().all()
            counts = connection.execute(
                select(class_memberships.c.class_id)
            ).scalars().all()
        member_counts: dict[str, int] = {}
        for class_id in counts:
            member_counts[class_id] = member_counts.get(class_id, 0) + 1
        return [{
            "classId": row["class_id"],
            "name": row["name"],
            "subject": row["subject"],
            "gradeBand": row["grade_band"],
            "memberCount": member_counts.get(row["class_id"], 0),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        } for row in rows]

    def add_member(self, *, class_id: str, learner_id: str, display_name: str, joined_at: float) -> dict[str, Any]:
        self._ensure_initialized()
        with self.engine.begin() as connection:
            if not connection.execute(
                select(learning_classes.c.class_id).where(learning_classes.c.class_id == class_id)
            ).first():
                raise LookupError("班级不存在")
            self._upsert(
                connection,
                class_memberships,
                {
                    "class_id": class_id,
                    "learner_id": learner_id,
                    "display_name": display_name,
                    "joined_at": joined_at,
                },
                ["class_id", "learner_id"],
                ["display_name"],
            )
        return {"classId": class_id, "learnerId": learner_id, "displayName": display_name, "joinedAt": joined_at}

    def create_assignment(
        self,
        *,
        assignment_id: str,
        class_id: str,
        publication_id: str,
        title: str | None,
        due_at: float | None,
        created_at: float,
    ) -> dict[str, Any]:
        self._ensure_initialized()
        with self.engine.begin() as connection:
            if not connection.execute(
                select(learning_classes.c.class_id).where(learning_classes.c.class_id == class_id)
            ).first():
                raise LookupError("班级不存在")
            publication = connection.execute(
                select(lesson_publications).where(lesson_publications.c.publication_id == publication_id)
            ).mappings().first()
            if not publication or publication["status"] != "published":
                raise LookupError("只能指派已发布互动试卷")
            connection.execute(assignments.insert().values(
                assignment_id=assignment_id,
                class_id=class_id,
                publication_id=publication_id,
                title=title or publication["title"],
                due_at=due_at,
                status="active",
                created_at=created_at,
                updated_at=created_at,
            ))
        return self.get_assignment(assignment_id)  # type: ignore[return-value]

    def get_assignment(self, assignment_id: str) -> dict[str, Any] | None:
        self._ensure_initialized()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(assignments, lesson_publications.c.title.label("publication_title"), lesson_publications.c.lesson_ids_json)
                .select_from(assignments.join(
                    lesson_publications,
                    assignments.c.publication_id == lesson_publications.c.publication_id,
                ))
                .where(assignments.c.assignment_id == assignment_id)
            ).mappings().first()
        return self._assignment_from_row(row) if row else None

    def list_assignments_for_learner(self, learner_id: str, *, now: float | None = None) -> list[dict[str, Any]]:
        self._ensure_initialized()
        current_time = now if now is not None else time.time()
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(assignments, lesson_publications.c.title.label("publication_title"), lesson_publications.c.lesson_ids_json, learning_classes.c.name.label("class_name"))
                .select_from(assignments.join(
                    class_memberships,
                    assignments.c.class_id == class_memberships.c.class_id,
                ).join(
                    lesson_publications,
                    assignments.c.publication_id == lesson_publications.c.publication_id,
                ).join(
                    learning_classes,
                    assignments.c.class_id == learning_classes.c.class_id,
                ))
                .where(
                    class_memberships.c.learner_id == learner_id,
                    assignments.c.status == "active",
                )
                .order_by(assignments.c.due_at.asc().nullslast(), assignments.c.created_at.desc())
            ).mappings().all()
            sessions = connection.execute(
                select(learning_sessions)
                .where(
                    learning_sessions.c.learner_id == learner_id,
                    learning_sessions.c.assignment_id.in_([row["assignment_id"] for row in rows] or ["__none__"]),
                )
            ).mappings().all()
            attempts = connection.execute(
                select(exercise_attempts.c.session_id, exercise_attempts.c.question_id, exercise_attempts.c.created_at)
                .where(exercise_attempts.c.session_id.in_([row["session_id"] for row in sessions] or ["__none__"]))
            ).mappings().all()
        attempts_by_session: dict[str, set[str]] = {}
        for attempt in attempts:
            attempts_by_session.setdefault(attempt["session_id"], set()).add(attempt["question_id"])
        sessions_by_assignment: dict[str, list[Any]] = {}
        for session in sessions:
            sessions_by_assignment.setdefault(session["assignment_id"], []).append(session)
        result = []
        for row in rows:
            assignment = self._assignment_from_row(row)
            assignment_sessions = sessions_by_assignment.get(row["assignment_id"], [])
            attempted_questions = {
                question_id
                for session in assignment_sessions
                for question_id in attempts_by_session.get(session["session_id"], set())
            }
            session = max(assignment_sessions, key=lambda item: item["updated_at"]) if assignment_sessions else None
            attempted_count = len(attempted_questions)
            assignment.update({
                "learnerId": learner_id,
                "sessionId": session["session_id"] if session else None,
                "attemptedCount": attempted_count,
                "progress": round(attempted_count / assignment["questionCount"], 4) if assignment["questionCount"] else 0,
                "learnerStatus": self._progress_status(
                    attempted_count,
                    assignment["questionCount"],
                    assignment["dueAt"],
                    current_time,
                ),
            })
            result.append(assignment)
        return result

    def add_assignment_session(self, *, assignment_id: str, learner_id: str) -> dict[str, Any]:
        """Validate that a learner belongs to the assignment before starting a session."""
        self._ensure_initialized()
        with self.engine.connect() as connection:
            row = connection.execute(
                select(assignments.c.assignment_id, assignments.c.publication_id)
                .select_from(assignments.join(
                    class_memberships,
                    assignments.c.class_id == class_memberships.c.class_id,
                ))
                .where(
                    assignments.c.assignment_id == assignment_id,
                    assignments.c.status == "active",
                    class_memberships.c.learner_id == learner_id,
                )
            ).mappings().first()
        if not row:
            raise LookupError("作业不存在或学生不属于该班级")
        return dict(row)

    def class_dashboard(self, class_id: str, *, assignment_id: str | None = None, now: float | None = None) -> dict[str, Any]:
        self._ensure_initialized()
        current_time = now if now is not None else time.time()
        with self.engine.connect() as connection:
            class_row = connection.execute(
                select(learning_classes).where(learning_classes.c.class_id == class_id)
            ).mappings().first()
            if not class_row:
                raise LookupError("班级不存在")
            members = connection.execute(
                select(class_memberships).where(class_memberships.c.class_id == class_id)
                .order_by(class_memberships.c.display_name.asc(), class_memberships.c.learner_id.asc())
            ).mappings().all()
            assignment_query = select(assignments, lesson_publications.c.title.label("publication_title"), lesson_publications.c.lesson_ids_json).select_from(
                assignments.join(lesson_publications, assignments.c.publication_id == lesson_publications.c.publication_id)
            ).where(assignments.c.class_id == class_id)
            if assignment_id:
                assignment_query = assignment_query.where(assignments.c.assignment_id == assignment_id)
            else:
                assignment_query = assignment_query.order_by(assignments.c.created_at.desc()).limit(1)
            assignment_row = connection.execute(assignment_query).mappings().first()
            if not assignment_row:
                raise LookupError("班级还没有作业")
            assignment = self._assignment_from_row(assignment_row)
            learner_ids = [member["learner_id"] for member in members]
            sessions = connection.execute(
                select(learning_sessions)
                .where(
                    learning_sessions.c.assignment_id == assignment["assignmentId"],
                    learning_sessions.c.learner_id.in_(learner_ids or ["__none__"]),
                )
            ).mappings().all()
            session_ids = [session["session_id"] for session in sessions]
            attempt_rows = connection.execute(
                select(exercise_attempts.c.session_id, exercise_attempts.c.question_id)
                .where(exercise_attempts.c.session_id.in_(session_ids or ["__none__"]))
            ).mappings().all()
            lesson_rows = connection.execute(
                select(lesson_documents.c.question_json, lesson_documents.c.knowledge_points_json, lesson_documents.c.title)
                .where(lesson_documents.c.lesson_id.in_(assignment["lessonIds"] or ["__none__"]))
            ).mappings().all()
            # mastery_states is imported lazily to keep the main schema import grouping readable.
            from persistence.schema import mastery_states
            states = connection.execute(
                select(mastery_states)
                .where(mastery_states.c.learner_id.in_(learner_ids or ["__none__"]))
            ).mappings().all()
        points = self._publication_points(assignment, lesson_rows)
        point_ids = {point["knowledgePointId"] for point in points}
        state_by_learner = {
            (state["learner_id"], state["knowledge_point_id"]): state
            for state in states
            if state["knowledge_point_id"] in point_ids
        }
        attempted_by_learner: dict[str, set[str]] = {}
        session_by_learner: dict[str, Any] = {}
        for session in sessions:
            previous = session_by_learner.get(session["learner_id"])
            if previous is None or session["updated_at"] > previous["updated_at"]:
                session_by_learner[session["learner_id"]] = session
        for row in attempt_rows:
            learner_id = next((session["learner_id"] for session in sessions if session["session_id"] == row["session_id"]), None)
            if learner_id:
                attempted_by_learner.setdefault(learner_id, set()).add(row["question_id"])
        students = []
        for member in members:
            learner_id = member["learner_id"]
            attempted_count = len(attempted_by_learner.get(learner_id, set()))
            scores = [float(state["score"]) for (student_id, _), state in state_by_learner.items() if student_id == learner_id and state.get("evidence_count", 0)]
            students.append({
                "learnerId": learner_id,
                "displayName": member["display_name"],
                "sessionId": session_by_learner.get(learner_id, {}).get("session_id"),
                "attemptedCount": attempted_count,
                "questionCount": assignment["questionCount"],
                "progress": round(attempted_count / assignment["questionCount"], 4) if assignment["questionCount"] else 0,
                "status": self._progress_status(attempted_count, assignment["questionCount"], assignment["dueAt"], current_time),
                "averageMastery": round(sum(scores) / len(scores), 4) if scores else None,
            })

        knowledge_point_views = []
        for point in points:
            state_values = [
                state_by_learner[(member["learner_id"], point["knowledgePointId"])]
                for member in members
                if (member["learner_id"], point["knowledgePointId"]) in state_by_learner
                and state_by_learner[(member["learner_id"], point["knowledgePointId"])].get("evidence_count", 0)
            ]
            distribution = {"notStarted": len(members) - len(state_values), "needsSupport": 0, "developing": 0, "mastered": 0}
            for state in state_values:
                score = float(state["score"])
                if score >= 0.7:
                    distribution["mastered"] += 1
                elif score >= 0.4:
                    distribution["developing"] += 1
                else:
                    distribution["needsSupport"] += 1
            knowledge_point_views.append({
                **point,
                "observedStudentCount": len(state_values),
                "averageScore": round(sum(float(state["score"]) for state in state_values) / len(state_values), 4) if state_values else None,
                "distribution": distribution,
            })
        completed_count = sum(student["status"] == "completed" for student in students)
        started_count = sum(student["status"] != "not_started" for student in students)
        return {
            "class": {
                "classId": class_row["class_id"],
                "name": class_row["name"],
                "subject": class_row["subject"],
                "gradeBand": class_row["grade_band"],
            },
            "assignment": assignment,
            "summary": {
                "memberCount": len(members),
                "startedCount": started_count,
                "completedCount": completed_count,
                "completionRate": round(completed_count / len(members), 4) if members else None,
            },
            "students": students,
            "knowledgePoints": knowledge_point_views,
            "metricDefinition": "掌握度只统计已有作答证据的学生；未开始不等于掌握度为 0。",
        }

    @staticmethod
    def _assignment_from_row(row: Any) -> dict[str, Any]:
        lesson_ids = decode_json(row.get("lesson_ids_json") or row.get("lesson_ids_json", [])) or []
        return {
            "assignmentId": row["assignment_id"],
            "classId": row["class_id"],
            "publicationId": row["publication_id"],
            "title": row["title"],
            "publicationTitle": row.get("publication_title") or row["title"],
            "className": row.get("class_name"),
            "dueAt": row.get("due_at"),
            "status": row["status"],
            "lessonIds": lesson_ids,
            "questionCount": len(lesson_ids),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _progress_status(attempted_count: int, question_count: int, due_at: float | None, now: float) -> str:
        if question_count > 0 and attempted_count >= question_count:
            return "completed"
        if attempted_count > 0:
            return "overdue" if due_at is not None and due_at < now else "in_progress"
        return "overdue" if due_at is not None and due_at < now else "not_started"

    @staticmethod
    def _publication_points(assignment: dict[str, Any], lesson_rows: Sequence[Any]) -> list[dict[str, Any]]:
        points_by_id: dict[str, dict[str, Any]] = {}
        for lesson in lesson_rows:
            payload = decode_json(lesson["question_json"]) or {}
            question = payload.get("question") or {}
            name = normalize_knowledge_point_name(
                question.get("knowledgePoint")
                or (decode_json(lesson["knowledge_points_json"]) or [None])[0]
                or lesson["title"]
            )
            point_id = knowledge_point_id(assignment["publicationId"], name)
            points_by_id[point_id] = {"knowledgePointId": point_id, "knowledgePoint": name}
        return sorted(points_by_id.values(), key=lambda item: item["knowledgePoint"])
