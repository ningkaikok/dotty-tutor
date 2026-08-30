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

    def test_assignment_filters_student_and_dashboard_uses_evidence(self) -> None:
        created = self.client.post("/api/classes", json={"name": "初二数学一班"})
        self.assertEqual(created.status_code, 200)
        class_id = created.json()["classId"]
        self.client.post(f"/api/classes/{class_id}/members", json={"learnerId": "learner-a", "displayName": "小安"})
        self.client.post(f"/api/classes/{class_id}/members", json={"learnerId": "learner-b", "displayName": "小北"})
        assignment = self.client.post(
            f"/api/classes/{class_id}/assignments",
            json={"publicationId": "paper-classroom", "dueAt": 4_000_000_000},
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
        assignment_id = self.client.post(
            f"/api/classes/{class_id}/assignments",
            json={"publicationId": "paper-classroom"},
        ).json()["assignmentId"]
        outsider = self.client.post(
            "/api/learning/sessions",
            json={"learnerId": "outsider", "publicationId": "paper-classroom", "assignmentId": assignment_id},
        )
        self.assertEqual(outsider.status_code, 404)


if __name__ == "__main__":
    unittest.main()
