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
        self.planner = planner
        self.service = service
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

    def _prepared_plan(self) -> tuple[str, dict]:
        class_id = self.client.post("/api/classes", json={"name": "一次函数班"}).json()["classId"]
        self.client.post(f"/api/classes/{class_id}/members", json={"learnerId": "learner-a", "displayName": "小安"})
        plan = self.client.post(
            f"/api/classes/{class_id}/assignment-plans", json={"publicationId": "source-paper"},
        ).json()
        return class_id, plan

    def _forget_final_plan_marker(self, plan_id: str) -> None:
        """抹掉来源计划上的最终计划标记。

        这精确复现并发窗口：第二个请求在第一个请求写回标记之前就读过计划，
        因此短路检查对它不生效。用状态复现而不是起线程，避免不确定的测试。
        """
        store = self.planner.planning_store
        current = store.get(plan_id)["result"]
        store.update_result(
            plan_id,
            {key: value for key, value in current.items() if key != "personalizedFinalPlanId"},
            updated_at=2,
        )

    def _published_personalized_papers(self) -> list[str]:
        return [
            item["publicationId"]
            for item in self.store.list_publications(status="published")
            if item["publicationId"] != "source-paper"
        ]

    def test_concurrent_generation_publishes_exactly_one_paper(self) -> None:
        """并发窗口：两次生成都跑完模型，但只能有一份已发布试卷和一个最终计划。"""
        class_id, plan = self._prepared_plan()
        first = self.service.generate(class_id=class_id, plan_id=plan["planId"], question_count=1)
        self._forget_final_plan_marker(plan["planId"])
        second = self.service.generate(class_id=class_id, plan_id=plan["planId"], question_count=1)

        # 两次都真的调用了模型（这正是竞态的代价），但结果必须收敛到同一个计划。
        self.assertEqual(self.runtime.calls, 2)
        self.assertEqual(second["planId"], first["planId"])
        self.assertEqual(second["publicationId"], first["publicationId"])
        # 输掉竞争的那份试卷不得进入已发布列表，否则老师会看到两份并可能误派。
        self.assertEqual(self._published_personalized_papers(), [first["publicationId"]])

    def test_losing_paper_stays_unassignable(self) -> None:
        """输掉竞争的试卷停在 draft，指派接口只接受 published，因此永远派不出去。"""
        class_id, plan = self._prepared_plan()
        first = self.service.generate(class_id=class_id, plan_id=plan["planId"], question_count=1)
        self._forget_final_plan_marker(plan["planId"])
        self.service.generate(class_id=class_id, plan_id=plan["planId"], question_count=1)

        drafts = [
            item["publicationId"]
            for item in self.store.list_publications(status="draft")
            if item["publicationId"] not in {"source-paper", first["publicationId"]}
        ]
        self.assertEqual(len(drafts), 1)
        assigned = self.client.post(
            f"/api/classes/{class_id}/assignments",
            json={
                "planId": first["planId"], "publicationId": drafts[0],
                "sourceFingerprint": first["sourceFingerprint"], "confirmWarnings": True,
            },
        )
        self.assertIn(assigned.status_code, {404, 409})

    def test_retry_after_a_failed_publish_heals_the_plan(self) -> None:
        """声明成功但发布失败时，重试必须补完发布，而不是卡在无法指派的状态。"""
        class_id, plan = self._prepared_plan()
        first = self.service.generate(class_id=class_id, plan_id=plan["planId"], question_count=1)
        # 复现"计划已存在、试卷却没发布出去"的中断状态。
        self.store.update_publication_status(first["publicationId"], "archived")
        self.store.update_publication_status(first["publicationId"], "draft")
        self._forget_final_plan_marker(plan["planId"])

        healed = self.service.generate(class_id=class_id, plan_id=plan["planId"], question_count=1)
        self.assertEqual(healed["planId"], first["planId"])
        publication = self.store.load_publication(first["publicationId"])
        self.assertEqual(publication["status"], "published")

    def test_final_plan_id_is_derived_from_the_source_plan(self) -> None:
        """ID 必须可由来源计划推导——这正是主键能充当原子声明的前提。"""
        from application.services.personalized_assignment import personalized_plan_id

        class_id, plan = self._prepared_plan()
        final = self.service.generate(class_id=class_id, plan_id=plan["planId"], question_count=1)
        self.assertEqual(final["planId"], personalized_plan_id(plan["planId"]))


if __name__ == "__main__":
    unittest.main()
