"""Business acceptance for source-bound lesson media on real PostgreSQL storage."""

from __future__ import annotations

import io
import json
import math
import unittest
import wave
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from application.chapter_jobs import build_chapter_registry
from application.job_registry import merge_registries
from application.job_worker import JobWorker
from application.lesson_media_jobs import build_lesson_media_registry
from application.services.chapter_courses import ChapterCourseService
from application.services.lesson_media import LessonMediaService
from persistence.app_store import AppStore
from persistence.job_store import JobStore
from persistence.lesson_media_store import LessonMediaStore
from routers.chapter_routes import build_chapter_router
from routers.lesson_media_routes import build_lesson_media_router
from tests.postgres_test_support import PostgresTestCase


class ReviewedTutorialRuntime:
    """Fixture for the external model boundary; it is not teaching-quality evidence."""

    def generate_json(self, prompt: str, schema: dict[str, Any], **kwargs: Any):
        source = json.loads(prompt.split("SOURCE_JSON=", 1)[1])
        page = source["pages"][0]
        sentence = page["sentences"][0]
        citation = {
            "sourceRevisionId": source["sourceRevisionId"],
            "page": page["page"],
            "sentenceId": sentence["sentenceId"],
            "quote": sentence["text"],
        }
        return {
            "sections": [
                {"kind": kind, "title": kind, "text": sentence["text"], "citations": [citation]}
                for kind in ("objectives", "explanation", "example", "summary")
            ]
        }, {"provider": "acceptance-fixture", "model": "not-a-teaching-result"}


class SpeechRuntimeFixture:
    """External TTS boundary fixture; rendered test audio is deliberately synthetic."""

    def __init__(self, duration_seconds: int = 15) -> None:
        self.duration_seconds = duration_seconds
        self.calls: list[str] = []
        self.languages: list[str] = []

    def synthesize(
        self,
        text: str,
        *,
        language: str,
        speaker: str,
        instruct: str,
        timeout_seconds: float | None = None,
    ):
        self.calls.append(text)
        self.languages.append(language)
        sample_rate = 24_000
        output = io.BytesIO()
        with wave.open(output, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(sample_rate)
            audio.writeframes(b"".join(
                int(900 * math.sin(2 * math.pi * 220 * frame / sample_rate)).to_bytes(2, "little", signed=True)
                for frame in range(sample_rate * self.duration_seconds)
            ))
        return {"audio": output.getvalue(), "provider": "acceptance-fixture", "model": "synthetic-tone"}


class LessonMediaPostgresTests(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.app_store = AppStore(database_url=self.database_url, data_root=self.data_root)
        self.jobs = JobStore(database_url=self.database_url, data_root=self.data_root)
        self.media_store = LessonMediaStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.app_store.close)
        self.addCleanup(self.jobs.close)
        self.addCleanup(self.media_store.close)
        self.speech = SpeechRuntimeFixture()
        self.chapter_service = ChapterCourseService(
            self.app_store,
            jobs=self.jobs,
            generation_runtime=ReviewedTutorialRuntime(),
        )
        self.media_service = LessonMediaService(
            chapter_service=self.chapter_service,
            media_store=self.media_store,
            jobs=self.jobs,
            speech_runtime=self.speech,
        )
        app = FastAPI()
        app.include_router(build_chapter_router(self.chapter_service))
        app.include_router(build_lesson_media_router(self.media_service))
        self.client = self.enterContext(TestClient(app))
        registry = merge_registries(
            build_chapter_registry(self.chapter_service),
            build_lesson_media_registry(self.media_service),
        )
        self.worker = JobWorker(self.jobs, registry, worker_id="lesson-media-acceptance")

    def _published_tutorial(self, subject: str = "math") -> dict[str, Any]:
        text = (
            "等式两边同时加减同一个数，等式仍然成立。解 x + 2 = 5 时，两边同时减去 2，得到 x = 3。"
            if subject == "math"
            else "Mia moved to London. She enjoys reading, and she shares stories with her friends every weekend."
        )
        is_math = subject == "math"
        chapter = self.chapter_service.create({
            "subject": subject,
            "title": "等式与同解变形" if is_math else "Mia's weekend story",
            "teachingMode": "tutorial",
            "source": {
                "license": "CC BY 4.0" if is_math else "CC BY-NC-SA 4.0",
                "sourceTitle": "Elementary Algebra 2e" if is_math else "English Language Arts",
                "sourceUrl": (
                    "https://openstax.org/details/books/elementary-algebra-2e"
                    if is_math else "https://openstax.org/details/books/english-language-arts"
                ),
                "attributionText": "Access for free at openstax.org.",
                "pages": [{"page": 214 if is_math else 123, "text": text}],
            },
        })
        queued = self.chapter_service.enqueue_ai_generation(
            chapter["chapterId"],
            expected_record_version=chapter["recordVersion"],
            runtime_snapshot={
                "version": 1,
                "generation": {"provider": "mock", "model": "static-demo"},
                "ocr": {"provider": "pypdf"},
                "review": {"provider": "mock", "model": "static-demo"},
            },
        )
        generated = self.worker.run_once()
        self.assertEqual(generated["jobId"], queued["jobId"])
        self.assertEqual(generated["status"], "succeeded", generated.get("lastError"))
        managed = self.chapter_service.get(chapter["chapterId"])
        lesson = managed["lessons"][0]
        self.chapter_service.review(
            chapter["chapterId"], lesson["lessonId"], "approve", "acceptance-teacher",
            expected_record_version=managed["recordVersion"],
        )
        publication = self.chapter_service.publish(chapter["chapterId"])
        return {
            "chapterId": chapter["chapterId"],
            "publicationId": publication["publicationId"],
            "lessonId": lesson["lessonId"],
            "sourceRevisionId": lesson["sourceRevisionId"],
        }

    def test_user_renders_and_reviews_a_source_bound_video_then_students_get_only_the_approved_publication(self) -> None:
        # Given a published tutorial whose four teaching blocks were explicitly approved
        published = self._published_tutorial()

        # When a teacher creates a source snapshot and the shared Worker renders its clips
        created = self.client.post("/api/lesson-media/drafts", json=published)
        self.assertEqual(created.status_code, 201, created.text)
        draft = created.json()
        self.assertEqual(draft["sourceRevisionId"], published["sourceRevisionId"])
        self.assertEqual(len(draft["scenes"]), 4)
        self.assertEqual(draft["attributionText"], "Access for free at openstax.org.")
        self.assertEqual(draft["distributionScope"], "student")
        queued = self.client.post(f"/api/lesson-media/{draft['mediaId']}/render", json={"expectedVersion": 1})
        self.assertEqual(queued.status_code, 202, queued.text)
        finished = self.worker.run_once()
        self.assertEqual(finished["status"], "succeeded", finished.get("lastError"))
        rendered = self.client.get(f"/api/lesson-media/{draft['mediaId']}").json()
        self.assertEqual(rendered["status"], "consistency_review_required")
        self.assertTrue(rendered["consistencyReview"]["passed"])
        self.assertEqual(len(self.speech.calls), 4)
        self.assertGreaterEqual(rendered["durationSeconds"], 60)
        self.assertLessEqual(rendered["durationSeconds"], 120)
        self.assertEqual(self.client.get(
            f"/api/chapters/{published['chapterId']}/media/published",
            params={"publicationId": published["publicationId"], "lessonId": published["lessonId"]},
        ).json()["items"], [])

        # Then a teacher approval and separate publish action make the exact media version available
        approved = self.client.post(
            f"/api/lesson-media/{draft['mediaId']}/review",
            json={"decision": "approve", "displayName": "课堂教师", "note": "画面、旁白和引用已核对"},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        published_media = self.client.post(
            f"/api/lesson-media/{draft['mediaId']}/publish",
            json={"displayName": "课堂教师"},
        )
        self.assertEqual(published_media.status_code, 200, published_media.text)
        student_media = self.client.get(
            f"/api/chapters/{published['chapterId']}/media/published",
            params={"publicationId": published["publicationId"], "lessonId": published["lessonId"]},
        )
        self.assertEqual(student_media.status_code, 200, student_media.text)
        self.assertEqual(student_media.json()["items"][0]["mediaId"], draft["mediaId"])
        self.assertEqual(published_media.json()["reviews"][0]["actor"]["actorId"], "demo:unverified")
        media_item = student_media.json()["items"][0]
        video = self.client.get(media_item["videoUrl"])
        captions = self.client.get(media_item["captionsUrl"])
        caption_download = self.client.get(media_item["captionsDownloadUrl"])
        self.assertEqual(video.status_code, 200)
        self.assertIn("inline", video.headers["content-disposition"])
        self.assertEqual(captions.status_code, 200)
        self.assertIn("WEBVTT", captions.text)
        self.assertIn("inline", captions.headers["content-disposition"])
        self.assertIn("attachment", caption_download.headers["content-disposition"])
        self.assertEqual(
            self.client.post(f"/api/chapters/{published['chapterId']}/attempts", json={
                "attemptId": "watch-is-not-an-attempt",
                "lessonId": published["lessonId"],
                "questionId": published["lessonId"],
            }).status_code,
            409,
        )

    def test_user_cannot_publish_a_short_render_when_the_voice_duration_misses_the_bounded_video_contract(self) -> None:
        # Given a published lesson and an external TTS runtime that finishes too quickly
        published = self._published_tutorial("english")
        self.speech.duration_seconds = 1

        # When the teacher renders the same source-bound storyboard
        draft = self.client.post("/api/lesson-media/drafts", json=published).json()
        self.assertEqual(draft["distributionScope"], "local_only")
        self.client.post(f"/api/lesson-media/{draft['mediaId']}/render", json={"expectedVersion": 1})
        failed = self.worker.run_once()
        self.assertEqual(failed["status"], "succeeded", failed.get("lastError"))
        rendered = self.client.get(f"/api/lesson-media/{draft['mediaId']}").json()

        # Then the duration gate blocks human approval and the learner projection remains empty
        self.assertEqual(rendered["status"], "consistency_failed")
        self.assertFalse(rendered["consistencyReview"]["passed"])
        self.assertEqual(self.speech.languages, ["English"] * 4)
        self.assertEqual(
            self.client.post(f"/api/lesson-media/{draft['mediaId']}/review", json={
                "decision": "approve", "displayName": "课堂教师",
            }).status_code,
            409,
        )
        self.assertEqual(self.client.get(
            f"/api/chapters/{published['chapterId']}/media/published",
            params={"publicationId": published["publicationId"], "lessonId": published["lessonId"]},
        ).json()["items"], [])

    def test_user_creates_a_clip_revision_without_mutating_the_published_artifact(self) -> None:
        # Given a rendered version with a complete source snapshot and immutable artifacts
        published = self._published_tutorial()
        original = self.client.post("/api/lesson-media/drafts", json=published).json()
        self.client.post(f"/api/lesson-media/{original['mediaId']}/render", json={"expectedVersion": 1})
        self.worker.run_once()
        prior = self.client.get(f"/api/lesson-media/{original['mediaId']}").json()
        original_hash = prior["artifactSha256"]
        first_scene = prior["scenes"][0]

        # When a teacher revises one narrated clip
        revised = self.client.post(f"/api/lesson-media/{original['mediaId']}/revisions", json={
            "expectedVersion": 1,
            "sceneEdits": [{"sceneId": first_scene["sceneId"], "narration": "补充说明：先检查等式两边的运算是否相同。"}],
        })

        # Then a new source-bound version exists while the old version keeps its original bytes and text
        self.assertEqual(revised.status_code, 201, revised.text)
        next_version = revised.json()
        self.assertEqual(next_version["version"], 2)
        self.assertEqual(next_version["scenes"][0]["narration"], "补充说明：先检查等式两边的运算是否相同。")
        self.assertEqual(self.client.get(f"/api/lesson-media/{original['mediaId']}").json()["artifactSha256"], original_hash)
        self.assertNotEqual(next_version["mediaId"], original["mediaId"])


if __name__ == "__main__":
    unittest.main()
