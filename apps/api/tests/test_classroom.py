from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from persistence.app_store import AppStore
from routers.classroom_routes import build_classroom_router
from routers.learning_routes import build_learning_router


class ClassroomWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.store = AppStore(database_url=f"sqlite+pysqlite:///{root / 'classroom.sqlite3'}", data_root=root)
        self.store.save_lesson({
            "lessonId": "q-classroom-1",
            "title": "一次函数",
            "version": 1,
            "status": "draft",
            "knowledgePoints": ["一次函数"],
            "blocks": [],
            "questionPayload": {
                "question": {
                    "id": "q-classroom-1",
                    "knowledgePoint": "一次函数",
                    "prompt": "下列函数中，随 x 增大而增大的是？",
                },
                "quality": {"status": "ready"},
            },
        })
        self.store.create_publication(
            publication_id="paper-classroom",
            title="一次函数练习",
            source_upload_id=None,
            lesson_ids=["q-classroom-1"],
            status="draft",
            created_at=1,
        )
        self.store.update_publication_status("paper-classroom", "in_review")
        self.store.update_publication_status("paper-classroom", "published")
        app = FastAPI()
        app.include_router(build_learning_router(store=self.store))
        app.include_router(build_classroom_router(store=self.store))
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def create_plan(self, class_id: str, *, confirm_warnings: bool = True) -> dict:
        response = self.client.post(
            f"/api/classes/{class_id}/assignment-plans",
            json={"publicationId": "paper-classroom"},
        )
        self.assertEqual(response.status_code, 200)
        plan = response.json()
        plan["confirmWarnings"] = confirm_warnings
        return plan

    def test_assignment_filters_student_and_dashboard_uses_evidence(self) -> None:
        created = self.client.post("/api/classes", json={"name": "初二数学一班"})
        self.assertEqual(created.status_code, 200)
        class_id = created.json()["classId"]
        self.client.post(f"/api/classes/{class_id}/members", json={"learnerId": "learner-a", "displayName": "小安"})
        self.client.post(f"/api/classes/{class_id}/members", json={"learnerId": "learner-b", "displayName": "小北"})
        plan = self.create_plan(class_id)
        assignment = self.client.post(
            f"/api/classes/{class_id}/assignments",
            json={"planId": plan["planId"], "publicationId": "paper-classroom", "sourceFingerprint": plan["sourceFingerprint"], "confirmWarnings": plan["confirmWarnings"], "dueAt": 4_000_000_000},
        )
        self.assertEqual(assignment.status_code, 200)
        assignment_id = assignment.json()["assignmentId"]

        tasks = self.client.get("/api/assignments?learnerId=learner-a")
        self.assertEqual(tasks.status_code, 200)
        self.assertEqual(tasks.json()["items"][0]["learnerStatus"], "not_started")
        session = self.client.post(
            "/api/learning/sessions",
            json={"learnerId": "learner-a", "publicationId": "paper-classroom", "assignmentId": assignment_id},
        )
        self.assertEqual(session.status_code, 200)
        recorded = self.client.post(
            f"/api/learning/sessions/{session.json()['sessionId']}/attempts",
            json={"questionId": "q-classroom-1", "assessment": "correct", "createdAt": 2},
        )
        self.assertEqual(recorded.status_code, 200)

        dashboard = self.client.get(f"/api/classes/{class_id}/dashboard?assignmentId={assignment_id}")
        self.assertEqual(dashboard.status_code, 200)
        body = dashboard.json()
        self.assertEqual(body["summary"], {"memberCount": 2, "startedCount": 1, "completedCount": 1, "completionRate": 0.5})
        student_a = next(student for student in body["students"] if student["learnerId"] == "learner-a")
        self.assertEqual(student_a["status"], "completed")
        point = body["knowledgePoints"][0]
        self.assertEqual(point["distribution"], {"notStarted": 1, "needsSupport": 0, "developing": 1, "mastered": 0})

        tasks_after = self.client.get("/api/assignments?learnerId=learner-a").json()["items"]
        self.assertEqual(tasks_after[0]["learnerStatus"], "completed")
        self.assertEqual(tasks_after[0]["progress"], 1)

    def test_assignment_session_requires_member_and_matching_publication(self) -> None:
        class_id = self.client.post("/api/classes", json={"name": "初二数学二班"}).json()["classId"]
        plan = self.create_plan(class_id)
        assignment_id = self.client.post(
            f"/api/classes/{class_id}/assignments",
            json={"planId": plan["planId"], "publicationId": "paper-classroom", "sourceFingerprint": plan["sourceFingerprint"], "confirmWarnings": plan["confirmWarnings"]},
        ).json()["assignmentId"]
        outsider = self.client.post(
            "/api/learning/sessions",
            json={"learnerId": "outsider", "publicationId": "paper-classroom", "assignmentId": assignment_id},
        )
        self.assertEqual(outsider.status_code, 404)

    def test_teacher_review_is_append_only_and_overrides_dashboard_projection(self) -> None:
        class_id = self.client.post("/api/classes", json={"name": "复核班"}).json()["classId"]
        self.client.post(f"/api/classes/{class_id}/members", json={"learnerId": "learner-a", "displayName": "小安"})
        plan = self.create_plan(class_id)
        assignment_id = self.client.post(
            f"/api/classes/{class_id}/assignments",
            json={
                "planId": plan["planId"],
                "publicationId": "paper-classroom",
                "sourceFingerprint": plan["sourceFingerprint"],
                "confirmWarnings": True,
            },
        ).json()["assignmentId"]
        session_id = self.client.post(
            "/api/learning/sessions",
            json={"learnerId": "learner-a", "publicationId": "paper-classroom", "assignmentId": assignment_id},
        ).json()["sessionId"]
        self.client.post(
            f"/api/learning/sessions/{session_id}/attempts",
            json={"questionId": "q-classroom-1", "assessment": "incorrect", "createdAt": 2},
        )
        before = self.client.get(f"/api/classes/{class_id}/dashboard?assignmentId={assignment_id}").json()
        point_id = before["knowledgePoints"][0]["knowledgePointId"]
        reviewed = self.client.post(
            f"/api/classes/{class_id}/assignments/{assignment_id}/reviews",
            json={"learnerId": "learner-a", "questionId": "q-classroom-1", "knowledgePointId": point_id, "action": "reviewed"},
        )
        self.assertEqual(reviewed.status_code, 200)
        overturned = self.client.post(
            f"/api/classes/{class_id}/assignments/{assignment_id}/reviews",
            json={
                "learnerId": "learner-a",
                "questionId": "q-classroom-1",
                "knowledgePointId": point_id,
                "action": "overturned",
                "correctedAssessment": "correct",
                "note": "教师确认答案应判对",
            },
        )
        self.assertEqual(overturned.status_code, 200)
        override = self.client.post(
            f"/api/classes/{class_id}/assignments/{assignment_id}/reviews",
            json={"learnerId": "learner-a", "knowledgePointId": point_id, "action": "mastery_override", "masteryScore": 0.9},
        )
        self.assertEqual(override.status_code, 200)
        after = self.client.get(f"/api/classes/{class_id}/dashboard?assignmentId={assignment_id}").json()
        self.assertEqual(after["reviewMetrics"], {
            "judgedCount": 1,
            "reviewedCount": 1,
            "overturnedCount": 1,
            "reviewRate": 1.0,
            "overturnRate": 1.0,
            "overrideCount": 1,
        })
        self.assertEqual(after["knowledgePoints"][0]["distribution"]["mastered"], 1)
        self.assertEqual(after["knowledgePoints"][0]["overriddenStudentCount"], 1)
        self.assertEqual(after["knowledgePoints"][0]["evidence"][0]["assessment"], "incorrect")
        self.assertEqual(after["knowledgePoints"][0]["evidence"][0]["reviewStatus"], "overturned")
        self.assertEqual(after["knowledgePoints"][0]["evidence"][0]["correctedAssessment"], "correct")

    def test_legacy_assignment_payload_is_rejected(self) -> None:
        class_id = self.client.post("/api/classes", json={"name": "初二数学三班"}).json()["classId"]
        response = self.client.post(
            f"/api/classes/{class_id}/assignments",
            json={"publicationId": "paper-classroom"},
        )
        self.assertEqual(response.status_code, 422)

    def test_plan_warning_stops_creation_and_confirmation_is_idempotent(self) -> None:
        class_id = self.client.post("/api/classes", json={"name": "确认班"}).json()["classId"]
        plan = self.create_plan(class_id, confirm_warnings=False)
        blocked = self.client.post(
            f"/api/classes/{class_id}/assignments",
            json={"planId": plan["planId"], "publicationId": "paper-classroom", "sourceFingerprint": plan["sourceFingerprint"], "confirmWarnings": False},
        )
        self.assertEqual(blocked.status_code, 409)
        confirmed = self.client.post(
            f"/api/classes/{class_id}/assignments",
            json={"planId": plan["planId"], "publicationId": "paper-classroom", "sourceFingerprint": plan["sourceFingerprint"], "confirmWarnings": True},
        )
        repeated = self.client.post(
            f"/api/classes/{class_id}/assignments",
            json={"planId": plan["planId"], "publicationId": "paper-classroom", "sourceFingerprint": plan["sourceFingerprint"], "confirmWarnings": True},
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json()["assignmentId"], confirmed.json()["assignmentId"])
        self.assertEqual(len(self.client.get(f"/api/classes/{class_id}").json()["assignments"]), 1)

    def test_plan_becomes_stale_when_class_evidence_changes(self) -> None:
        class_id = self.client.post("/api/classes", json={"name": "变更班"}).json()["classId"]
        plan = self.create_plan(class_id)
        self.client.post(f"/api/classes/{class_id}/members", json={"learnerId": "new", "displayName": "新同学"})
        response = self.client.post(
            f"/api/classes/{class_id}/assignments",
            json={"planId": plan["planId"], "publicationId": "paper-classroom", "sourceFingerprint": plan["sourceFingerprint"], "confirmWarnings": True},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("失效", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
