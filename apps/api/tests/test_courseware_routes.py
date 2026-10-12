from __future__ import annotations

import unittest
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from application.errors import AppError
from domain.contracts.courseware import CoursewareAttemptRequest
from routers.courseware_routes import build_courseware_router
from tests.auth_test_support import environment


def public_courseware_with_private_fields() -> dict[str, Any]:
    return {
        "coursewareId": "course-1",
        "publicationId": "publication-1",
        "version": 2,
        "subject": "english",
        "title": "Reading workshop",
        "sourceTitle": "Open textbook",
        "sourceUrl": "https://example.org/textbook",
        "sourceLicense": "CC BY 4.0",
        "attributionText": "Attribution for classroom use",
        "sourceDistributionScope": "classroom",
        "durationMinutes": 10,
        "sections": [{
            "sectionId": "section-1",
            "title": "Read",
            "purpose": "Build understanding",
            "estimatedMinutes": 10,
            "pageIds": ["page-1"],
        }],
        "pages": [{
            "pageId": "page-1",
            "sectionId": "section-1",
            "pageKind": "activity",
            "layout": "text_focus",
            "title": "Check your understanding",
            "objectiveIds": [],
            "sourceRefs": [{
                "sourceRevisionId": "revision-1",
                "page": 1,
                "sentenceIds": ["sentence-1"],
                "regionIds": [],
                "assetId": None,
            }],
            "attributionText": "Page attribution",
            "teacherNotes": "Private facilitator notes",
            "revealSteps": [{"revealId": "reveal-1", "content": "Private worked answer"}],
            "blocks": [{
                "kind": "activity",
                "blockId": "block-1",
                "activityId": "activity-1",
                "activityKind": "short_answer",
                "instructions": "Explain your choice.",
                "assessmentMode": "objective",
                "choices": [],
                "authorship": "teacher_authored",
                "answerKey": "Private answer",
                "acceptedAnswers": ["Private answer"],
                "teacherRubric": "Private rubric",
            }],
            "estimatedMinutes": 10,
        }],
        "assets": [],
        "status": "published",
    }


class CoursewareRouteService:
    """Small in-memory service boundary for HTTP behavior tests."""

    def __init__(self) -> None:
        self.attempts_by_learner: dict[str, list[dict[str, Any]]] = {}
        self.review_actors: list[str] = []

    def get_published(self, courseware_id: str, publication_id: str | None = None) -> dict[str, Any]:
        if courseware_id != "course-1":
            raise LookupError("已发布课件不存在")
        return public_courseware_with_private_fields()

    def attempt(
        self,
        courseware_id: str,
        *,
        learner_id: str,
        request: CoursewareAttemptRequest,
    ) -> dict[str, Any]:
        saved = {
            "attemptId": f"attempt-{len(self.attempts_by_learner.get(learner_id, [])) + 1}",
            "publicationId": request.publicationId,
            "activityId": request.activityId,
            "assessment": "needs_review",
            "feedback": "已提交给教师查看。",
            "createdAt": 1_800_000_000.0,
        }
        self.attempts_by_learner.setdefault(learner_id, []).append(saved)
        return saved

    def attempts_for(self, learner_id: str) -> list[dict[str, Any]]:
        return self.attempts_by_learner.get(learner_id, [])

    def get_document(self, courseware_id: str) -> dict[str, Any]:
        if courseware_id == "missing":
            raise LookupError("课件不存在")
        return {}

    def review_document(self, courseware_id: str, **values: Any) -> dict[str, Any]:
        self.review_actors.append(values["actor_id"])
        return {
            "coursewareId": courseware_id,
            "chapterId": "chapter-1",
            "planVersion": 1,
            "sourceRevisionId": "revision-1",
            "pageStart": 1,
            "pageEnd": 1,
            "subject": "english",
            "title": "A source-grounded page",
            "durationMinutes": 10,
            "sections": [{"sectionId": "section-1", "title": "Read", "estimatedMinutes": 10, "pageIds": ["page-1"]}],
            "pages": [{
                "pageId": "page-1",
                "sectionId": "section-1",
                "pageKind": "explanation",
                "layout": "text_focus",
                "title": "Read together",
                "sourceRefs": [{"sourceRevisionId": "revision-1", "page": 1, "sentenceIds": ["sentence-1"]}],
                "blocks": [{"kind": "text", "blockId": "block-1", "text": "Source-backed text", "authorship": "teacher_authored"}],
                "teacherNotes": "Private teacher note",
                "revealSteps": [],
                "estimatedMinutes": 10,
                "generationStatus": "completed",
                "reviewStatus": "approved",
                "version": 1,
            }],
            "assets": [],
            "reviewIssues": [],
            "recordVersion": 2,
            "status": "ready",
            "generationStatus": "completed",
            "sourceReadiness": "ready",
            "reviewStatus": "ready",
            "createdAt": 1.0,
            "updatedAt": 2.0,
        }


class CoursewareRouteBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = CoursewareRouteService()
        app = FastAPI()
        app.state.auth_mode = "protected"

        # Simulate the trusted actor installed by app_factory authentication.
        @app.middleware("http")
        async def trusted_session(request: Request, call_next):
            role = request.headers.get("x-test-role")
            if role:
                request.state.actor = {
                    "role": role,
                    "sessionId": request.headers.get("x-test-session", "session-test"),
                    "learnerId": request.headers.get("x-test-learner", "learner-test"),
                }
            return await call_next(request)

        @app.exception_handler(AppError)
        async def app_error_handler(request: Request, error: AppError) -> JSONResponse:
            return JSONResponse(status_code=error.status_code, content=error.to_problem("courseware-test"))

        app.include_router(build_courseware_router(self.service))
        self.client = TestClient(app)

    def test_user_posts_practice_as_the_session_learner_even_if_body_claims_another_identity(self) -> None:
        # Given a student session and an untrusted identity field in the request body.
        with environment({"AUTH_MODE": "protected"}):
            response = self.client.post(
                "/api/courseware/course-1/attempts",
                headers={"x-test-role": "student", "x-test-learner": "learner-actual"},
                json={
                    "publicationId": "publication-1",
                    "activityId": "activity-1",
                    "commandId": "attempt-command-1",
                    "response": {"text": "My response"},
                    "learnerId": "learner-attacker",
                },
            )

        # Then the response and persisted practice both belong to the session learner.
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["assessment"], "needs_review")
        self.assertEqual(len(self.service.attempts_for("learner-actual")), 1)
        self.assertEqual(self.service.attempts_for("learner-attacker"), [])

    def test_user_receives_only_the_student_projection_of_a_published_courseware(self) -> None:
        # Given a publication service response that accidentally contains private fields.
        response = self.client.get("/api/courseware/course-1/published")

        # Then the student contract keeps the teaching prompt and strips notes, reveals and answers.
        self.assertEqual(response.status_code, 200, response.text)
        page = response.json()["pages"][0]
        activity = page["blocks"][0]
        self.assertEqual(activity["instructions"], "Explain your choice.")
        self.assertNotIn("teacherNotes", page)
        self.assertNotIn("revealSteps", page)
        self.assertNotIn("answerKey", activity)
        self.assertNotIn("acceptedAnswers", activity)
        self.assertNotIn("teacherRubric", activity)

    def test_user_gets_a_stable_not_found_problem_for_a_missing_draft(self) -> None:
        # Given a teacher opening a draft that has been removed.
        response = self.client.get(
            "/api/courseware/missing",
            headers={"x-test-role": "teacher", "x-test-session": "teacher-session"},
        )

        # Then the boundary returns the application error shape without a traceback.
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["errorCode"], "COURSEWARE_NOT_FOUND")
        self.assertEqual(response.json()["message"], "课件不存在")

    def test_user_cannot_open_a_teacher_draft_with_a_student_session(self) -> None:
        # Given a student session that asks for the teacher-only draft endpoint.
        response = self.client.get(
            "/api/courseware/course-1",
            headers={"x-test-role": "student", "x-test-learner": "learner-actual"},
        )

        # Then the router denies access before exposing any draft data.
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["errorCode"], "TEACHER_SESSION_REQUIRED")

    def test_user_records_review_under_the_authenticated_teacher_not_a_body_identity(self) -> None:
        # Given a teacher session and a review request carrying a forged actor field.
        response = self.client.post(
            "/api/courseware/course-1/review",
            headers={"x-test-role": "teacher", "x-test-session": "teacher-session-9"},
            json={
                "decision": "approve",
                "expectedRecordVersion": 1,
                "pageIds": ["page-1"],
                "note": "Source checked",
                "actorId": "forged-teacher",
            },
        )

        # Then the saved review uses the server-owned teacher session identity.
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.service.review_actors, ["teacher-session-9"])


if __name__ == "__main__":
    unittest.main()
