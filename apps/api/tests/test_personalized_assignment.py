from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from application.services.assignment_planning import AssignmentPlanningService
from application.services.personalized_assignment import PersonalizedAssignmentService
from persistence.app_store import AppStore
from persistence.assignment_planning_store import AssignmentPlanningStore
from persistence.mistake_store import MistakeStore
from routers.classroom_routes import build_classroom_router
from routers.learning_routes import build_learning_router
from tests.postgres_test_support import PostgresTestCase


class FakePersonalizedRuntime:
    def __init__(self, *, fallback: bool = False) -> None:
        self.fallback = fallback
        self.calls = 0

    def generate_json(self, prompt: str, schema: dict, max_tokens: int = 2800):
        self.calls += 1
        if self.fallback:
            return {"questions": []}, {"provider": "mock", "fallback": True}
        return {
            "questions": [{
                "planningTopicKey": "一次函数",
                "lesson": {
                    "questionType": "choice",
                    "chapter": "一次函数",
                    "knowledgePoint": "一次函数",
                    "prompt": "下列函数中，随 x 增大而增大的是？",
                    "correctAnswers": ["A"],
                    "options": ["y=x", "y=-x"],
                },
            }],
        }, {"provider": "test", "model": "fake", "fallback": False}


class PersonalizedAssignmentTests(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        root = self.data_root
        self.store = AppStore(database_url=self.database_url, data_root=root)
        self.addCleanup(self.store.close)
        self.store.save_lesson({
            "lessonId": "source-question",
            "title": "一次函数",
            "status": "draft",
            "knowledgePoints": ["一次函数"],
            "blocks": [],
            "questionPayload": {"question": {
                "id": "source-question", "questionType": "choice", "knowledgePoint": "一次函数",
                "prompt": "原题：判断一次函数的增减性。", "correctAnswers": ["A"], "options": ["y=x", "y=-x"],
                "contentBlocks": [{"type": "text", "text": "原题：判断一次函数的增减性。"}],
            }, "quality": {"status": "ready"}},
        })
        self.store.create_publication(
            publication_id="source-paper", title="原试卷", source_upload_id=None,
            lesson_ids=["source-question"], status="draft", created_at=1,
        )
        self.store.update_publication_status("source-paper", "in_review")
        self.store.update_publication_status("source-paper", "published")
        planner = AssignmentPlanningService(
            store=self.store, planning_store=AssignmentPlanningStore(engine=self.store.engine),
            mistake_store=MistakeStore(engine=self.store.engine, data_root=root),
        )
        runtime = FakePersonalizedRuntime()
        service = PersonalizedAssignmentService(store=self.store, planning_service=planner, model_runtime=runtime)
        app = FastAPI()
        app.include_router(build_learning_router(store=self.store))
        app.include_router(build_classroom_router(store=self.store, planning_service=planner, personalized_service=service))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.runtime = runtime
        planner.mistake_store.create({
            "mistakeId": "class-mistake", "learnerId": "learner-a", "sourceFilename": "source.png",
            "contentType": "image/png", "sourceImagePath": "", "sourceImageUrl": "",
            "questionPayload": {"question": {"id": "mistake-q", "prompt": "错题"}},
            "chapter": "一次函数", "knowledgePoint": "一次函数", "errorReason": "concept",
            "status": "unmastered", "createdAt": 1, "updatedAt": 1,
        })

    def test_generates_new_publication_and_final_plan_idempotently(self) -> None:
        class_id = self.client.post("/api/classes", json={"name": "一次函数班"}).json()["classId"]
        self.client.post(f"/api/classes/{class_id}/members", json={"learnerId": "learner-a", "displayName": "小安"})
        assignment_plan = self.client.post(
            f"/api/classes/{class_id}/assignment-plans", json={"publicationId": "source-paper"},
        ).json()
        response = self.client.post(
            f"/api/classes/{class_id}/assignment-plans/{assignment_plan['planId']}/personalized",
            json={"questionCount": 1},
        )
        self.assertEqual(response.status_code, 200, response.text)
        final = response.json()
        self.assertNotEqual(final["publicationId"], "source-paper")
        repeated = self.client.post(
            f"/api/classes/{class_id}/assignment-plans/{assignment_plan['planId']}/personalized",
            json={"questionCount": 1},
        )
        self.assertEqual(repeated.json()["planId"], final["planId"])
        self.assertEqual(self.runtime.calls, 1)
        assigned = self.client.post(
            f"/api/classes/{class_id}/assignments",
            json={"planId": final["planId"], "publicationId": final["publicationId"], "sourceFingerprint": final["sourceFingerprint"], "confirmWarnings": True},
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)


if __name__ == "__main__":
    unittest.main()
