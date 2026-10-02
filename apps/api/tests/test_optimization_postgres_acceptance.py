"""User-facing invariants exercised with concurrent, real PostgreSQL transactions."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from persistence.auth_store import AuthSessionStore
from persistence.learning_store import LearningStore
from persistence.tutoring_store import ConcurrentTurnError, TutoringStore
from tests.postgres_test_support import PostgresTestCase


class OptimizationPostgresAcceptanceTests(PostgresTestCase):
    def test_user_concurrent_turns_commit_only_one_complete_pair(self) -> None:
        """Given one thread, when turns race, then stale output cannot overwrite it."""
        store = TutoringStore(engine=self.engine)
        thread_id = store.create_or_get("concurrent-mistake", "student-a")["threadId"]
        ready = threading.Barrier(2)

        def submit(label: str) -> str:
            ready.wait(timeout=10)
            try:
                store.append_turn(
                    thread_id, student_content=label, input_mode="text",
                    assistant_content=label, assessment="partial", action={}, model_run={},
                    stage="diagnose", hint_level=1, summary=label, expected_message_count=0,
                    request_key=label, request_hash=label,
                    replay_response={"reply": label},
                )
                return label
            except ConcurrentTurnError:
                return "stale"

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(submit, label) for label in ("turn-a", "turn-b")]
            outcomes = [future.result(timeout=30) for future in futures]
        self.assertEqual(outcomes.count("stale"), 1)
        winner = next(value for value in outcomes if value != "stale")
        thread = store.get(thread_id)
        self.assertEqual(thread["messageCount"], 2)
        self.assertEqual([message["content"] for message in thread["messages"]], [winner, winner])
        self.assertEqual(thread["summary"], winner)
        self.assertIsNotNone(store.get_turn_request(thread_id, winner))
        loser = "turn-b" if winner == "turn-a" else "turn-a"
        self.assertIsNone(store.get_turn_request(thread_id, loser))

    def test_user_invitation_can_be_consumed_only_once_under_race(self) -> None:
        """Given one invite, when two clients redeem it, then only one session exists."""
        store = AuthSessionStore(engine=self.engine)
        _, teacher = store.create_session(role="teacher", learner_id=None)
        token = store.create_invite(learner_id="student-a", teacher_session_id=teacher["sessionId"])
        ready = threading.Barrier(2)

        def redeem():
            ready.wait(timeout=10)
            return store.consume_invite(token)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(redeem) for _ in range(2)]
            outcomes = [future.result(timeout=30) for future in futures]
        sessions = [value for value in outcomes if value is not None]
        self.assertEqual(len(sessions), 1)
        raw_token, session = sessions[0]
        self.assertEqual(store.resolve(raw_token)["learnerId"], "student-a")
        self.assertTrue(store.revoke(session["sessionId"]))
        self.assertIsNone(store.resolve(raw_token))

    def test_user_published_content_stays_fixed_when_save_races_publication(self) -> None:
        """Given a reviewed paper, when save races publish, then its published content stays fixed."""
        store = LearningStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(store.engine.dispose)
        original = {
            "lessonId": "concurrent-lesson", "title": "并发验收", "version": 1,
            "status": "draft", "knowledgePoints": [], "blocks": [],
            "questionPayload": {"question": {"id": "q", "answerSpec": {"expected": "2"}},
                                "quality": {"status": "ready"}},
            "guideCards": [],
        }
        changed = {**original, "title": "发布前更新"}
        store.save_lesson(original)
        store.create_publication(publication_id="concurrent-paper", title="试卷", source_upload_id=None,
                                 lesson_ids=[original["lessonId"]], status="in_review", created_at=1.0)
        ready = threading.Barrier(2)

        def publish():
            ready.wait(timeout=10)
            return store.update_publication_status("concurrent-paper", "published")

        def save():
            ready.wait(timeout=10)
            try:
                store.save_lesson(changed)
                return "saved"
            except ValueError:
                return "immutable"

        with ThreadPoolExecutor(max_workers=2) as executor:
            published = executor.submit(publish)
            saved = executor.submit(save)
            publication = published.result(timeout=30)
            saved.result(timeout=30)
        self.assertEqual(publication["status"], "published")
        published_title = publication["lessons"][0]["title"]
        self.assertEqual(store.load_publication("concurrent-paper")["lessons"][0]["title"], published_title)
        with self.assertRaises(ValueError):
            store.save_lesson({**original, "title": "覆盖历史"})
        self.assertEqual(store.load_lesson(original["lessonId"])["title"], published_title)
