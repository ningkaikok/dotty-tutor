from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from persistence.tutoring_store import (
    TUTOR_TURN_IDEMPOTENCY_RETENTION_SECONDS,
    ConcurrentTurnError,
    TutoringStore,
    tutoring_metadata,
)


class TutoringStoreConcurrencyTests(unittest.TestCase):
    def test_stale_generated_turn_cannot_overwrite_newer_thread_state(self) -> None:
        engine = create_engine(
            "sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        tutoring_metadata.create_all(engine)
        store = TutoringStore(engine=engine)
        thread = store.create_or_get("mistake-a", "student-a")
        turn = {
            "student_content": "答案是 2", "input_mode": "text", "assistant_content": "先检查算式。",
            "assessment": "partial", "action": {}, "model_run": {}, "stage": "diagnose",
            "hint_level": 0, "summary": "学生给出答案。",
        }
        store.append_turn(thread["threadId"], **turn, expected_message_count=0)
        with self.assertRaises(ConcurrentTurnError):
            store.append_turn(thread["threadId"], **turn, expected_message_count=0)
        persisted = store.get(thread["threadId"])
        self.assertEqual(persisted["messageCount"], 2)
        self.assertEqual(len(persisted["messages"]), 2)
        engine.dispose()

    def test_successful_request_keeps_an_idempotent_response_snapshot(self) -> None:
        engine = create_engine(
            "sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        tutoring_metadata.create_all(engine)
        store = TutoringStore(engine=engine)
        thread = store.create_or_get("mistake-b", "student-b")
        store.append_turn(
            thread["threadId"],
            student_content="答案是 2", input_mode="text", assistant_content="再检查这一步。",
            assessment="partial", action={"assessment": "partial"}, model_run={"provider": "mock"},
            stage="diagnose", hint_level=1, summary="已给出提示。", expected_message_count=0,
            request_key="key-digest", request_hash="body-digest",
            replay_response={"reply": {"reply": "再检查这一步。"}, "action": {"assessment": "partial"}},
            now=1_000,
        )
        replay = store.get_turn_request(thread["threadId"], "key-digest", now=1_000)
        self.assertEqual(replay["requestHash"], "body-digest")
        self.assertEqual(replay["response"]["reply"]["reply"], "再检查这一步。")
        self.assertIsNone(store.get_turn_request(
            thread["threadId"], "key-digest", now=1_000 + TUTOR_TURN_IDEMPOTENCY_RETENTION_SECONDS + 1,
        ))
        self.assertEqual(store.get(thread["threadId"])["messageCount"], 2)
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
