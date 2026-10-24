from __future__ import annotations

import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from tempfile import TemporaryDirectory
from threading import Event, Lock
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import update

from persistence.app_store import AppStore
from persistence.mistake_store import MistakeStore, mistake_items
from persistence.review_store import ReviewStore
from routers.continuous_learning_routes import build_continuous_learning_router
from tests.postgres_test_support import PostgresTestCase


class FakeLearningData:
    def __init__(self) -> None:
        self.assignments: list[dict[str, Any]] = []
        self.mistakes: list[dict[str, Any]] = []
        self.reviews: list[dict[str, Any]] = []
        self.upgraded: list[str] = []
        self.started_reviews: dict[str, dict[str, Any]] = {}
        self.publications: list[dict[str, Any]] = []
        self.publication_details: dict[str, dict[str, Any]] = {}
        self.free_progress: list[dict[str, Any]] = []

    def list_assignments_for_learner(self, learner_id: str, *, now: float) -> list[dict[str, Any]]:
        return [item for item in self.assignments if item["learnerId"] == learner_id]

    def list(self, learner_id: str) -> list[dict[str, Any]]:
        return [item for item in self.mistakes if item["learnerId"] == learner_id and item["status"] != "archived"]

    def get(self, mistake_id: str) -> dict[str, Any] | None:
        mistake = next((item for item in self.mistakes if item["mistakeId"] == mistake_id), None)
        return mistake or next((item for item in self.reviews if item["taskId"] == mistake_id), None)

    def activate_published_attempt(self, mistake_id: str, learner_id: str) -> dict[str, Any] | None:
        item = self.get(mistake_id)
        if not item or item["learnerId"] != learner_id or item["contentType"] != "application/vnd.dotty.publication+json":
            return None
        if item["status"] == "pending_confirmation":
            item["status"] = "unmastered"
            self.upgraded.append(mistake_id)
        return item

    def list_for_learner(self, learner_id: str) -> list[dict[str, Any]]:
        return [item for item in self.reviews if item["learnerId"] == learner_id]

    def list_for_mistake(self, mistake_id: str) -> list[dict[str, Any]]:
        return [item for item in self.reviews if item["mistakeId"] == mistake_id]

    def get_review_mistake(self, mistake_id: str) -> dict[str, Any] | None:
        return self.get(mistake_id)

    def list_free_publication_progress(self, learner_id: str) -> list[dict[str, Any]]:
        return list(self.free_progress) if learner_id == "student-a" else []

    def list_publications(self, status: str | None = None) -> list[dict[str, Any]]:
        return list(self.publications)

    def load_publication(self, publication_id: str) -> dict[str, Any] | None:
        return self.publication_details.get(publication_id)

    def is_chapter_publication(self, _publication_id: str) -> bool:
        return False

    def list_chapters(self) -> list[dict[str, Any]]:
        return []

    def claim_start(self, task_id: str, *, now: float) -> str:
        review = next((item for item in self.reviews if item["taskId"] == task_id), None)
        if not review:
            return "missing"
        if review["status"] == "ready":
            return "ready"
        if review["status"] != "scheduled":
            return "in_progress"
        review.update(status="generating", startedAt=now)
        return "claimed"

    def finish_start(self, task_id: str, *, claimed_at: float, question_payload: dict[str, Any], model_run: dict[str, Any]) -> dict[str, Any] | None:
        review = next((item for item in self.reviews if item["taskId"] == task_id), None)
        if not review or review["status"] != "generating" or review["startedAt"] != claimed_at:
            return None
        review.update(status="ready", questionPayload=question_payload)
        self.started_reviews[task_id] = review
        return review

    def release_start(self, task_id: str, *, claimed_at: float) -> bool:
        review = next((item for item in self.reviews if item["taskId"] == task_id), None)
        if not review or review.get("startedAt") != claimed_at:
            return False
        review.update(status="scheduled", startedAt=None)
        return True


class FakeVariationService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate(self, mistake: dict[str, Any], sequence: int) -> dict[str, Any]:
        self.calls.append(mistake["mistakeId"])
        return {
            "questionPayload": {"question": {"id": "review-question", "prompt": "试着算一算。"}},
            "modelRun": {},
        }


def pending_photo(mistake_id: str = "photo-1") -> dict[str, Any]:
    return {
        "mistakeId": mistake_id,
        "learnerId": "student-a",
        "status": "pending_confirmation",
        "contentType": "image/png",
        "chapter": "第一章",
        "knowledgePoint": "一次方程",
        "questionPayload": {"question": {"id": "photo-question", "prompt": "$x+1=2$"}},
    }


def published_mistake(mistake_id: str = "paper-error-1", status: str = "unmastered") -> dict[str, Any]:
    return {
        "mistakeId": mistake_id,
        "learnerId": "student-a",
        "status": status,
        "contentType": "application/vnd.dotty.publication+json",
        "chapter": "第一章",
        "knowledgePoint": "一次方程",
        "questionPayload": {"question": {"id": "paper-question", "prompt": "$x+1=2$"}},
    }


class ContinuousLearningRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = FakeLearningData()
        self.variation = FakeVariationService()
        app = FastAPI()
        app.include_router(build_continuous_learning_router(
            assignment_store=self.data,
            learning_store=self.data,
            mistake_store=self.data,
            review_store=self.data,
            variation_service=self.variation,
        ))
        self.client = TestClient(app)

    def test_user_with_an_in_progress_assignment_resumes_it_before_other_work(self) -> None:
        # Given 一份正在做的作业、待订正错题和到期复习
        self.data.assignments = [{
            "assignmentId": "assignment-1", "publicationId": "paper-1", "learnerId": "student-a",
            "title": "正在做的作业", "learnerStatus": "in_progress", "attemptedCount": 2, "questionCount": 4,
        }]
        self.data.mistakes = [published_mistake()]
        self.data.reviews = [{
            "taskId": "review-1", "mistakeId": "paper-error-1", "learnerId": "student-a",
            "status": "scheduled", "dueAt": 1, "intervalDays": 1,
        }]

        # When 学生查看下一项
        response = self.client.get("/api/learning/next?learnerId=student-a")

        # Then 服务端作业进度优先决定继续位置，GET 不触发生成
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["next"]["kind"], "assignment")
        self.assertEqual(response.json()["next"]["assignmentId"], "assignment-1")
        self.assertEqual(self.variation.calls, [])

    def test_user_with_an_ambiguous_photo_must_confirm_it_before_tutoring(self) -> None:
        # Given 一道来自照片识别、尚未核对的错题
        self.data.mistakes = [pending_photo()]

        # When 首页只读查询下一项
        response = self.client.get("/api/learning/next?learnerId=student-a")

        # Then 仍要求核对原图，GET 和开始请求都不启动题目生成
        self.assertEqual(response.json()["next"]["kind"], "confirm_mistake")
        started = self.client.post("/api/learning/next/start", json={"learnerId": "student-a"})
        self.assertEqual(started.json()["next"]["kind"], "confirm_mistake")
        self.assertEqual(self.data.mistakes[0]["status"], "pending_confirmation")
        self.assertEqual(self.variation.calls, [])

    def test_user_with_a_older_published_wrong_answer_is_upgraded_only_after_start(self) -> None:
        # Given 旧版自动记录把已发布题误标为待确认
        self.data.mistakes = [published_mistake(status="pending_confirmation")]

        # When 读取入口，再明确开始学习
        preview = self.client.get("/api/learning/next?learnerId=student-a")
        self.assertEqual(preview.json()["next"]["kind"], "correct_mistake")
        self.assertEqual(self.data.mistakes[0]["status"], "pending_confirmation")
        started = self.client.post("/api/learning/next/start", json={"learnerId": "student-a"})

        # Then 已过发布门禁的题目进入订正，保留“错因未知”，不会制造人工确认记录
        self.assertEqual(started.json()["next"]["kind"], "correct_mistake")
        self.assertEqual(self.data.mistakes[0]["status"], "unmastered")
        self.assertIsNone(self.data.mistakes[0].get("errorReason"))
        self.assertEqual(self.data.upgraded, ["paper-error-1"])
        self.assertEqual(self.variation.calls, [])

    def test_user_starts_the_oldest_due_review_once_and_retries_resume_its_saved_question(self) -> None:
        # Given 没有未完成作业或待订正项，且有两道到期复习
        self.data.mistakes = [published_mistake()]
        self.data.mistakes[0]["status"] = "mastered"
        self.data.reviews = [
            {"taskId": "review-later", "mistakeId": "paper-error-1", "learnerId": "student-a", "status": "scheduled", "dueAt": 3, "intervalDays": 3},
            {"taskId": "review-due", "mistakeId": "paper-error-1", "learnerId": "student-a", "status": "scheduled", "dueAt": 1, "intervalDays": 1},
        ]

        # When 首页查询并开始复习
        preview = self.client.get("/api/learning/next?learnerId=student-a")
        self.assertEqual(preview.json()["next"]["taskId"], "review-due")
        self.assertEqual(self.variation.calls, [])
        first = self.client.post("/api/learning/next/start", json={"learnerId": "student-a"})
        second = self.client.post("/api/learning/next/start", json={"learnerId": "student-a"})

        # Then 一次显式开始生成一题，后续重试恢复同一题；另一任务保持待处理
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["next"]["status"], "ready")
        self.assertEqual(first.json()["next"]["questionPayload"]["question"]["id"], "review-question")
        self.assertEqual(second.json()["next"]["taskId"], "review-due")
        self.assertEqual(second.json()["next"]["status"], "ready")
        self.assertEqual(self.variation.calls, ["paper-error-1"])

    def test_user_without_assignments_can_resume_a_server_saved_free_paper_session(self) -> None:
        # Given 没有班级作业，但已有一道题作答的自由练习会话
        self.data.publications = [{"publicationId": "free-paper", "title": "自由练习", "status": "published", "lessonCount": 3, "updatedAt": 3}]
        self.data.publication_details = {"free-paper": {"lessons": [{"questionPayload": {"question": {"subject": "math"}}}]}}
        self.data.free_progress = [{"publicationId": "free-paper", "attemptedCount": 1, "sessionId": "server-session", "lastActivityAt": 4}]

        # When 学生只读下一项并明确开始
        preview = self.client.get("/api/learning/next?learnerId=student-a")
        started = self.client.post("/api/learning/next/start", json={"learnerId": "student-a"})

        # Then 仍可从统一入口进入练习，并携带服务端会话恢复题目位置
        self.assertEqual(preview.json()["next"]["kind"], "practice")
        self.assertEqual(started.json()["next"]["publicationId"], "free-paper")
        self.assertEqual(started.json()["next"]["attemptedCount"], 1)
        self.assertEqual(started.json()["next"]["sessionId"], "server-session")
        self.assertEqual(self.variation.calls, [])

    def test_user_with_more_than_one_hundred_mastered_items_still_sees_the_remaining_mistake(self) -> None:
        # Given 前面有大量已掌握条目，后面仍有一道待订正错题
        self.data.mistakes = [
            {**published_mistake(f"mastered-{index}"), "status": "mastered"}
            for index in range(105)
        ] + [published_mistake("remaining")]

        # When 查询下一项
        response = self.client.get("/api/learning/next?learnerId=student-a")

        # Then 不会因先截取一百条已完成记录而误报空队列
        self.assertEqual(response.json()["next"]["kind"], "correct_mistake")
        self.assertEqual(response.json()["next"]["mistakeId"], "remaining")


class ContinuousLearningPostgresTests(PostgresTestCase):
    def _build_app(self, store: AppStore, mistakes: MistakeStore, reviews: ReviewStore, variation_service: Any) -> FastAPI:
        app = FastAPI()
        app.include_router(build_continuous_learning_router(
            assignment_store=store,
            learning_store=store,
            mistake_store=mistakes,
            review_store=reviews,
            variation_service=variation_service,
        ))
        return app

    def _create_published_paper(self, store: AppStore, publication_id: str, questions: list[str]) -> None:
        for question_id in questions:
            store.save_lesson({
                "lessonId": question_id,
                "title": "数轴比较",
                "version": 1,
                "status": "draft",
                "sourceUploadId": None,
                "knowledgePoints": ["数轴上的大小比较"],
                "blocks": [],
                "questionPayload": {
                    "question": {
                        "id": question_id,
                        "subject": "math",
                        "questionType": "choice",
                        "prompt": "在数轴上比较两个数。",
                        "options": ["A. 1", "B. 2"],
                        "correctAnswer": "B",
                        "correctAnswers": ["B"],
                        "knowledgePoint": "数轴上的大小比较",
                    },
                    "quality": {"status": "ready"},
                },
                "guideCards": [],
            })
        store.create_publication(
            publication_id=publication_id,
            title="数轴练习",
            source_upload_id=None,
            lesson_ids=questions,
            status="draft",
            created_at=1,
        )
        store.update_publication_status(publication_id, "in_review")
        store.update_publication_status(publication_id, "published")

    def test_user_without_assignments_resumes_existing_server_session_even_after_local_state_is_lost(self) -> None:
        # Given 一套真实发布练习和服务器保存的半套自由练习记录
        with TemporaryDirectory() as directory:
            store = AppStore(database_url=self.database_url, data_root=directory)
            mistakes = MistakeStore(engine=store.engine, data_root=directory)
            reviews = ReviewStore(engine=store.engine)
            client = TestClient(self._build_app(store, mistakes, reviews, FakeVariationService()))
            self.addCleanup(client.close)
            self.addCleanup(store.close)
            self._create_published_paper(store, "free-paper", ["free-q1", "free-q2", "free-q3"])
            store.create_learning_session(
                session_id="server-free-session",
                learner_id="student-a",
                publication_id="free-paper",
                started_at=1,
            )
            store.record_exercise_attempt(
                attempt_id="free-attempt-1",
                session_id="server-free-session",
                question_id="free-q1",
                response={"text": "我选择(A)：1", "interactionResult": {"selectedOptions": ["A"]}},
                assessment="incorrect",
                hint_level=0,
                duration_ms=100,
                created_at=2,
            )

            # When 学生只依靠 /learn 的服务端下一项重新开始
            response = client.post("/api/learning/next/start", json={"learnerId": "student-a"})

            # Then 服务端返回同一会话指针与真实未完成数，本机存储不是进度真相
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["next"]["kind"], "practice")
            self.assertEqual(response.json()["next"]["attemptedCount"], 1)
            self.assertEqual(response.json()["next"]["sessionId"], "server-free-session")
            self.assertEqual(store.get_learning_session("server-free-session")["attempts"][0]["questionId"], "free-q1")

    def test_user_explicitly_starts_a_legacy_published_mistake_without_creating_confirmation_evidence(self) -> None:
        # Given 旧版本自动记录留下的已发布题待确认状态
        with TemporaryDirectory() as directory:
            store = AppStore(database_url=self.database_url, data_root=directory)
            mistakes = MistakeStore(engine=store.engine, data_root=directory)
            reviews = ReviewStore(engine=store.engine)
            self.addCleanup(store.close)
            mistakes.create({
                "mistakeId": "legacy-published",
                "learnerId": "student-a",
                "sourceFilename": "已发布练习",
                "contentType": "application/vnd.dotty.publication+json",
                "sourceImagePath": "",
                "sourceImageUrl": "",
                "questionPayload": {"question": {"id": "published-q", "prompt": "原题"}},
                "guideCards": [], "ocrRun": {}, "modelRun": {}, "originalAnswer": "答案",
                "chapter": "章节", "knowledgePoint": "知识点", "status": "unmastered",
                "confirmedAt": None, "createdAt": 1, "updatedAt": 1,
            })
            # create() applies current write rules; recreate the historical state
            # directly so this test exercises repair of rows written by old code.
            with store.engine.begin() as connection:
                connection.execute(
                    update(mistake_items)
                    .where(mistake_items.c.mistake_id == "legacy-published")
                    .values(status="pending_confirmation")
                )
            client = TestClient(self._build_app(store, mistakes, reviews, FakeVariationService()))
            self.addCleanup(client.close)

            # When 首页只读后，学生明确开始订正
            before_get = mistakes.get("legacy-published")
            preview = client.get("/api/learning/next?learnerId=student-a")
            after_get = mistakes.get("legacy-published")
            self.assertIsNotNone(before_get)
            self.assertIsNotNone(after_get)
            self.assertEqual(before_get["status"], "pending_confirmation")
            self.assertEqual(after_get["status"], before_get["status"])
            started = client.post("/api/learning/next/start", json={"learnerId": "student-a"})

            # Then GET 未写入，POST 只恢复流程状态，不伪造学生确认或错因
            self.assertEqual(preview.json()["next"]["kind"], "correct_mistake")
            self.assertEqual(started.json()["next"]["kind"], "correct_mistake")
            stored = mistakes.get("legacy-published")
            self.assertEqual(stored["status"], "unmastered")
            self.assertIsNone(stored["confirmedAt"])
            self.assertIsNone(stored["errorReason"])

    def test_user_retries_two_review_starts_without_duplicate_generation_or_question_replacement(self) -> None:
        # Given 一道真实到期复习，生成过程暂时占用一个数据库claim
        class BlockingVariationService:
            def __init__(self) -> None:
                self.entered = Event()
                self.release = Event()
                self.lock = Lock()
                self.calls = 0

            def generate(self, _mistake: dict[str, Any], _sequence: int) -> dict[str, Any]:
                with self.lock:
                    self.calls += 1
                self.entered.set()
                if not self.release.wait(10):
                    raise TimeoutError("test generation gate was not released")
                return {
                    "questionPayload": {"question": {"id": "winner-question", "prompt": "复习题", "answer": "secret"}},
                    "modelRun": {},
                }

        with TemporaryDirectory() as directory:
            store = AppStore(database_url=self.database_url, data_root=directory)
            mistakes = MistakeStore(engine=store.engine, data_root=directory)
            reviews = ReviewStore(engine=store.engine)
            self.addCleanup(store.close)
            mistakes.create({
                "mistakeId": "review-mistake",
                "learnerId": "student-a",
                "sourceFilename": "已确认错题",
                "contentType": "image/png",
                "sourceImagePath": "",
                "sourceImageUrl": "",
                "questionPayload": {"question": {"id": "review-source", "prompt": "原题"}},
                "guideCards": [], "ocrRun": {}, "modelRun": {}, "originalAnswer": "答案",
                "chapter": "章节", "knowledgePoint": "知识点", "status": "mastered",
                "confirmedAt": 1, "createdAt": 1, "updatedAt": 1,
            })
            reviews.schedule(mistake_id="review-mistake", learner_id="student-a", base_time=time.time() - 86_401, intervals=(1,))
            variation = BlockingVariationService()
            app = self._build_app(store, mistakes, reviews, variation)
            first_client = TestClient(app)
            second_client = TestClient(app)
            self.addCleanup(first_client.close)
            self.addCleanup(second_client.close)

            with ThreadPoolExecutor(max_workers=1) as executor:
                first = executor.submit(first_client.post, "/api/learning/next/start", json={"learnerId": "student-a"})
                self.assertTrue(variation.entered.wait(10))
                concurrent = second_client.post("/api/learning/next/start", json={"learnerId": "student-a"})
                variation.release.set()
                completed = first.result(timeout=20)

            # Then 竞争请求不会重复调用模型，winner题目被持久复用且答案仍不下发
            self.assertEqual(concurrent.status_code, 409)
            self.assertEqual(completed.status_code, 200)
            self.assertEqual(variation.calls, 1)
            winner_id = completed.json()["next"]["questionPayload"]["question"]["id"]
            self.assertEqual(winner_id, "winner-question")
            self.assertNotIn("answer", completed.json()["next"]["questionPayload"]["question"])
            retry = second_client.post("/api/learning/next/start", json={"learnerId": "student-a"})
            self.assertEqual(retry.json()["next"]["questionPayload"]["question"]["id"], winner_id)
            self.assertEqual(variation.calls, 1)


if __name__ == "__main__":
    unittest.main()
