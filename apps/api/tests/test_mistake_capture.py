from __future__ import annotations

import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from domain.questions.student_view import student_mistake_item
from persistence.mistake_store import MistakeStore, mistake_attributions
from persistence.tutoring_store import TutoringStore
from persistence.variation_store import VariationStore
from routers.mistake_routes import _public_item, build_mistake_router
from tests.postgres_test_support import PostgresTestCase


def fake_recognize(
    _source_path: Path,
    source_text: str,
    _asset_dir: Path,
    _asset_url_prefix: str,
) -> tuple[dict, list[dict], dict, dict]:
    prompt = source_text or "解方程 2x + 3 = 11"
    payload = {
        "question": {
            "id": "mistake-question-1",
            "questionType": "short-answer",
            "chapter": "一元一次方程",
            "knowledgePoint": "移项",
            "prompt": prompt,
            "givens": [],
            "options": [],
            "imageUrls": [],
            "contentBlocks": [
                {"id": "stem-1", "type": "text", "text": prompt, "sourceOrder": 0},
            ],
            "answer": "x=1",
            "verification": {"status": "verified"},
            "sourceProvenance": {"sourceBlockIds": ["block-1"]},
        },
        "lessonSteps": [],
        "architecture": {},
        "stageArtifacts": {"solution": {"answer": "x=1"}},
        "solution": {"answer": "x=1"},
        "verification": {"status": "verified"},
        "quality": {"status": "ready"},
        "review": {"status": "passed"},
        "sourceProvenance": {"sourceBlockIds": ["block-1"]},
        "cacheKey": "secret-cache-key",
        "modelRun": {"provider": "mock", "model": "fixture", "fallback": False},
    }
    return (
        payload,
        [{"level": 0, "hint": "先移项"}],
        {"provider": "manual", "mode": "fixture", "fallback": False},
        {"provider": "mock", "model": "fixture", "fallback": False},
    )


class MistakeCaptureApiTests(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = MistakeStore(
            database_url=self.database_url,
            data_root=self.data_root,
        )
        self.addCleanup(self.store.close)
        self.cleared: list[tuple[str, str]] = []
        app = FastAPI()
        app.include_router(build_mistake_router(
            store=self.store,
            recognize=fake_recognize,
            archive_cleanup=self._clear_tutor,
        ))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def _clear_tutor(self, mistake_id: str, learner_id: str) -> int:
        self.cleared.append((mistake_id, learner_id))
        return 1

    def assert_public_mistake(self, response: dict, *, prompt: str) -> None:
        """Every student mistake response keeps answer inputs but not pipeline internals."""
        self.assertNotIn("sourceImagePath", response)
        for key in ("modelRun", "ocrRun", "stageArtifacts", "solution", "verification", "quality", "review", "sourceProvenance", "cacheKey"):
            self.assertNotIn(key, response)
        payload = response["questionPayload"]
        self.assertEqual(payload["question"]["prompt"], prompt)
        self.assertEqual(payload["question"]["questionType"], "short-answer")
        self.assertNotIn("stageArtifacts", payload)
        self.assertNotIn("solution", payload)
        for key in ("modelRun", "verification", "quality", "review", "sourceProvenance", "cacheKey"):
            self.assertNotIn(key, payload)
        self.assertNotIn("answer", payload["question"])
        self.assertIn("originalAnswer", response)
        self.assertIn("options", payload["question"])

    def test_import_confirm_list_and_archive_mistake(self) -> None:
        response = self.client.post(
            "/api/mistakes/import",
            files={"file": ("equation.png", b"image-fixture", "image/png")},
            data={"sourceText": "解方程 x + 1 = 3", "originalAnswer": "x=1"},
        )
        self.assertEqual(response.status_code, 200)
        imported = response.json()
        self.assert_public_mistake(imported, prompt="解方程 x + 1 = 3")
        self.assertEqual(imported["status"], "pending_confirmation")
        self.assertEqual(imported["questionPayload"]["question"]["prompt"], "解方程 x + 1 = 3")
        self.assertNotIn("sourceImagePath", imported)

        source = self.client.get(imported["sourceImageUrl"])
        self.assertEqual(source.status_code, 200)
        self.assertEqual(source.content, b"image-fixture")

        confirmation = {
            "prompt": "解方程 $x + 1 = 3$",
            "originalAnswer": "x=1",
            "subject": "数学",
            "gradeBand": "初中",
            "chapter": "一元一次方程",
            "knowledgePoint": "移项",
            "errorReason": "calculation",
            "notes": "移项后符号写错",
        }
        confirmed_response = self.client.patch(
            f"/api/mistakes/{imported['mistakeId']}",
            json=confirmation,
        )
        self.assertEqual(confirmed_response.status_code, 200)
        confirmed = confirmed_response.json()
        self.assert_public_mistake(confirmed, prompt="解方程 $x + 1 = 3$")
        self.assertEqual(confirmed["status"], "unmastered")
        self.assertEqual(confirmed["errorReason"], "calculation")
        self.assertIsNotNone(confirmed["confirmedAt"])
        self.assertEqual(confirmed["questionPayload"]["question"]["prompt"], "解方程 $x + 1 = 3$")
        self.assertEqual(
            confirmed["questionPayload"]["question"]["contentBlocks"],
            [
                {"id": "stem-1", "type": "text", "text": "解方程 ", "sourceOrder": 0},
                {"id": "stem-2", "type": "math", "latex": "x + 1 = 3", "display": False, "sourceOrder": 1},
            ],
        )
        attributions = self.store.list_attributions(imported["mistakeId"])
        self.assertEqual([(item["source"], item["category"]) for item in attributions], [("self", "calculation")])

        listed = self.client.get("/api/mistakes").json()["items"]
        self.assertEqual([item["mistakeId"] for item in listed], [imported["mistakeId"]])
        self.assert_public_mistake(listed[0], prompt="解方程 $x + 1 = 3$")

        detail = self.client.get(f"/api/mistakes/{imported['mistakeId']}")
        self.assertEqual(detail.status_code, 200)
        self.assert_public_mistake(detail.json(), prompt="解方程 $x + 1 = 3$")

        archived = self.client.patch(
            f"/api/mistakes/{imported['mistakeId']}/archive",
            json={"archived": True},
        )
        self.assertEqual(archived.json()["status"], "archived")
        self.assert_public_mistake(archived.json(), prompt="解方程 $x + 1 = 3$")
        self.assertEqual(self.cleared, [(imported["mistakeId"], "local-demo")])
        self.assertEqual(self.client.get("/api/mistakes").json()["items"], [])

    def test_rejects_non_image_upload(self) -> None:
        response = self.client.post(
            "/api/mistakes/import",
            files={"file": ("answer.txt", b"not-an-image", "text/plain")},
        )
        self.assertEqual(response.status_code, 415)

    def test_confirmation_rejects_unknown_error_reason(self) -> None:
        response = self.client.post(
            "/api/mistakes/import",
            files={"file": ("equation.png", b"image-fixture", "image/png")},
        )
        mistake_id = response.json()["mistakeId"]
        invalid = self.client.patch(
            f"/api/mistakes/{mistake_id}",
            json={
                "prompt": "题目",
                "chapter": "方程",
                "knowledgePoint": "移项",
                "errorReason": "guess",
            },
        )
        self.assertEqual(invalid.status_code, 422)

    def test_confirmation_succeeds_without_error_reason(self) -> None:
        """错因归因迁移到陪练首轮：确认时不传 errorReason 也必须能保存。"""
        response = self.client.post(
            "/api/mistakes/import",
            files={"file": ("equation.png", b"image-fixture", "image/png")},
        )
        mistake_id = response.json()["mistakeId"]
        confirmed_response = self.client.patch(
            f"/api/mistakes/{mistake_id}",
            json={
                "prompt": "解方程 2x + 3 = 11",
                "chapter": "一元一次方程",
                "knowledgePoint": "移项",
            },
        )
        self.assertEqual(confirmed_response.status_code, 200)
        confirmed = confirmed_response.json()
        self.assertEqual(confirmed["status"], "unmastered")
        self.assertIsNone(confirmed["errorReason"])

    def test_confirmation_preserves_existing_self_assessment_when_omitted_or_null(self) -> None:
        response = self.client.post(
            "/api/mistakes/import",
            files={"file": ("equation.png", b"image-fixture", "image/png")},
        )
        mistake_id = response.json()["mistakeId"]
        base = {
            "prompt": "解方程 2x + 3 = 11",
            "chapter": "一元一次方程",
            "knowledgePoint": "移项",
            "errorReason": "calculation",
        }
        self.assertEqual(self.client.patch(f"/api/mistakes/{mistake_id}", json=base).status_code, 200)
        omitted = {key: value for key, value in base.items() if key != "errorReason"}
        self.assertEqual(self.client.patch(f"/api/mistakes/{mistake_id}", json=omitted).json()["errorReason"], "calculation")
        explicit_null = {**omitted, "errorReason": None}
        self.assertEqual(self.client.patch(f"/api/mistakes/{mistake_id}", json=explicit_null).json()["errorReason"], "calculation")
        self.assertEqual(len(self.store.list_attributions(mistake_id)), 1)

    def test_attribution_retries_are_idempotent_and_latest_ignores_pending(self) -> None:
        mistake_id = self.client.post(
            "/api/mistakes/import",
            files={"file": ("equation.png", b"image-fixture", "image/png")},
        ).json()["mistakeId"]
        self.store.update_ai_error_reason(
            mistake_id, category="calculation", confidence=0.7, updated_at=10
        )
        self.store.update_ai_error_reason(
            mistake_id, category="calculation", confidence=0.7, updated_at=10
        )
        self.store.update_ai_error_reason(
            mistake_id, category="calculation", confidence=0.8, updated_at=11
        )
        self.store.update_ai_error_reason(
            mistake_id, category="reading", confidence=0.9, updated_at=12
        )
        self.assertEqual(len(self.store.list_attributions(mistake_id)), 3)
        self.assertEqual(self.store.latest_attribution(mistake_id)["category"], "reading")

        with self.store.engine.begin() as connection:
            connection.execute(
                mistake_attributions.insert().values(
                    attribution_id="pending-attribution",
                    mistake_id=mistake_id,
                    source="teacher",
                    category="teacher-review",
                    confidence=1.0,
                    evidence_json={"source": "test"},
                    model_version=None,
                    created_at=99,
                    accepted_at=None,
                )
            )
        self.assertEqual(self.store.latest_attribution(mistake_id)["category"], "reading")

        confirmation = {
            "prompt": "解方程 2x + 3 = 11",
            "originalAnswer": "x=1",
            "subject": "数学",
            "gradeBand": "初中",
            "chapter": "一元一次方程",
            "knowledgePoint": "移项",
            "errorReason": "calculation",
            "notes": "移项后符号写错",
        }
        fresh_id = self.client.post(
            "/api/mistakes/import",
            files={"file": ("equation-2.png", b"image-fixture", "image/png")},
        ).json()["mistakeId"]
        self.store.confirm(fresh_id, confirmation, confirmed_at=20)
        self.store.confirm(fresh_id, confirmation, confirmed_at=20)
        self.store.confirm(fresh_id, confirmation, confirmed_at=21)
        self.assertEqual(len(self.store.list_attributions(fresh_id)), 2)

    def test_confirmation_succeeds_with_empty_chapter_and_knowledge_point(self) -> None:
        """章节/知识点不再强制：AI 已预填时，学生可以直接保存而不修改分类。"""
        response = self.client.post(
            "/api/mistakes/import",
            files={"file": ("equation.png", b"image-fixture", "image/png")},
        )
        mistake_id = response.json()["mistakeId"]
        confirmed_response = self.client.patch(
            f"/api/mistakes/{mistake_id}",
            json={
                "prompt": "解方程 2x + 3 = 11",
                "chapter": "",
                "knowledgePoint": "",
            },
        )
        self.assertEqual(confirmed_response.status_code, 200)
        confirmed = confirmed_response.json()
        self.assertEqual(confirmed["status"], "unmastered")
        self.assertEqual(confirmed["chapter"], "")
        self.assertEqual(confirmed["knowledgePoint"], "")

    def test_archive_keeps_learning_evidence_clears_thread_and_restore_starts_new_thread(self) -> None:
        """归档是错题软删除；陪练上下文清理，但验证证据必须可追溯。"""
        mistakes = self.store
        tutoring = TutoringStore(engine=self.engine)
        variations = VariationStore(engine=self.engine)
        now = 1.0
        mistakes.create({
            "mistakeId": "archive-mistake",
            "learnerId": "local-demo",
            "sourceFilename": "source.png",
            "contentType": "image/png",
            "sourceImagePath": "",
            "sourceImageUrl": "",
            "questionPayload": {"question": {"id": "archive-question", "prompt": "题目"}},
            "guideCards": [], "ocrRun": {}, "modelRun": {}, "originalAnswer": "B",
            "chapter": "章节", "knowledgePoint": "知识点", "status": "unmastered",
            "confirmedAt": now, "createdAt": now, "updatedAt": now,
        })
        variation = variations.create(
            mistake_id="archive-mistake", learner_id="local-demo", strategy="foundation",
            level="basic", question_payload={"question": {"id": "variation-question"}}, model_run={},
        )
        answered = variations.answer(
            variation["variationId"], response={"selectedOptions": ["(A)"]},
            assessment="correct", feedback="验证正确",
        )
        thread = tutoring.create_or_get("archive-mistake", "local-demo")
        tutoring.append_turn(
            thread["threadId"], student_content="我的答案是 A", input_mode="text",
            assistant_content="我们继续验证", assessment="correct", action={}, model_run={},
            stage="verify", hint_level=0, summary="已完成一轮",
        )
        app = FastAPI()
        app.include_router(build_mistake_router(
            store=mistakes, recognize=fake_recognize, archive_cleanup=tutoring.delete_for_mistake,
        ))
        client = TestClient(app)
        try:
            archived = client.patch("/api/mistakes/archive-mistake/archive", json={"archived": True})
            self.assertEqual(archived.status_code, 200)
            self.assertEqual(archived.json()["status"], "archived")
            self.assertEqual(mistakes.get("archive-mistake")["questionPayload"]["question"]["id"], "archive-question")
            self.assertEqual(variations.get(variation["variationId"])["response"], answered["response"])
            self.assertEqual(variations.get(variation["variationId"])["assessment"], "correct")
            self.assertIsNone(tutoring.get(thread["threadId"]))

            restored = client.patch("/api/mistakes/archive-mistake/archive", json={"archived": False})
            self.assertEqual(restored.status_code, 200)
            new_thread = tutoring.create_or_get("archive-mistake", "local-demo")
            self.assertNotEqual(new_thread["threadId"], thread["threadId"])
            self.assertEqual(new_thread["messages"], [])
        finally:
            client.close()


class MistakePublicProjectionTests(unittest.TestCase):
    def test_public_projection_is_safe_for_all_mistake_response_paths(self) -> None:
        """Import/list/detail/confirm/archive all share this student-safe boundary."""
        full_payload = {
            "question": {
                "id": "q-1",
                "questionType": "choice",
                "questionNumber": "7",
                "prompt": "原题题干",
                "options": ["A", "B"],
                "givens": ["已知条件"],
                "subQuestions": [{"id": "a", "prompt": "小问", "correctAnswer": "1"}],
                "imageReferences": ["img-1"],
                "correctAnswer": "B",
                "sourceProvenance": {"sourceBlockIds": ["block-1"]},
                "verification": {"status": "verified"},
            },
            "lessonSteps": [{"id": "step-1", "text": "先观察", "solution": "secret"}],
            "stageArtifacts": {"solution": {"answer": "B"}},
            "solution": {"answer": "B"},
            "verification": {"status": "verified"},
            "quality": {"status": "ready"},
            "review": {"status": "passed"},
            "sourceProvenance": {"sourceBlockIds": ["block-1"]},
            "cacheKey": "secret-cache-key",
            "modelRun": {"provider": "secret-provider"},
        }
        full_item = {
            "mistakeId": "mistake-1",
            "learnerId": "local-demo",
            "sourceFilename": "question.png",
            "contentType": "image/png",
            "sourceImagePath": "/private/secret/question.png",
            "sourceImageUrl": "/api/mistakes/mistake-1/source",
            "questionPayload": full_payload,
            "guideCards": [],
            "ocrRun": {"provider": "secret-ocr"},
            "modelRun": {"provider": "secret-model"},
            "quality": {"status": "ready"},
            "review": {"status": "passed"},
            "verification": {"status": "verified"},
            "originalAnswer": "A",
            "subject": "数学",
            "gradeBand": "初中",
            "chapter": "章节",
            "knowledgePoint": "知识点",
            "notes": "备注",
            "status": "pending_confirmation",
            "createdAt": 1.0,
            "updatedAt": 1.0,
        }

        public = _public_item(full_item)

        self.assertEqual(public["questionPayload"]["question"]["prompt"], "原题题干")
        self.assertEqual(public["questionPayload"]["question"]["options"], ["A", "B"])
        self.assertEqual(public["questionPayload"]["question"]["givens"], ["已知条件"])
        self.assertEqual(public["questionPayload"]["question"]["subQuestions"][0]["prompt"], "小问")
        self.assertEqual(public["originalAnswer"], "A")
        for key in (
            "sourceImagePath", "ocrRun", "modelRun", "quality", "review", "verification",
            "stageArtifacts", "solution", "sourceProvenance", "cacheKey",
        ):
            self.assertNotIn(key, public)
            self.assertNotIn(key, public["questionPayload"])
        self.assertNotIn("correctAnswer", public["questionPayload"]["question"])
        self.assertNotIn("correctAnswer", public["questionPayload"]["question"]["subQuestions"][0])
        self.assertNotIn("solution", public["questionPayload"]["lessonSteps"][0])

        # The adapter returns a new projection and never mutates the Store-owned full payload.
        self.assertIn("stageArtifacts", full_item["questionPayload"])
        self.assertIn("correctAnswer", full_item["questionPayload"]["question"])
        self.assertEqual(student_mistake_item(full_item), public)


if __name__ == "__main__":
    unittest.main()
