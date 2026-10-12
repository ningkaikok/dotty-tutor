"""Given/When/Then acceptance for durable voice drafts and response generations."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from persistence.schema import (
    chapter_english_attempts,
    exercise_attempts,
    mastery_states,
)
from persistence.voice_store import (
    VoiceSessionConflict,
    VoiceSessionNotFound,
    VoiceStore,
    VoiceTranscriptNotConfirmed,
)
from tests.postgres_test_support import PostgresTestCase


class VoiceTutorAcceptanceTests(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = VoiceStore(
            database_url=self.database_url, data_root=self.data_root
        )
        self.addCleanup(self.store.close)
        self.learner_id = "learner-voice-1"
        self.token = "voice-recovery-token-for-test"
        self.capability_hash = hashlib.sha256(self.token.encode()).hexdigest()
        self.session_id = "voice-session-1"

    def create_session(self, session_id: str | None = None) -> dict:
        return self.store.create(
            {
                "sessionId": session_id or self.session_id,
                "capabilityHash": self.capability_hash,
                "learnerId": self.learner_id,
                "chapterId": "chapter-1",
                "publicationId": "publication-1",
                "lessonId": "lesson-1",
                "blockId": "block-1",
                "contextLabel": "Chapter · Lesson · Step",
            }
        )

    def evidence_counts(self) -> dict[str, int]:
        with self.engine.connect() as connection:
            return {
                table.name: int(
                    connection.execute(
                        select(func.count()).select_from(table)
                    ).scalar_one()
                )
                for table in (
                    chapter_english_attempts,
                    exercise_attempts,
                    mastery_states,
                )
            }

    def test_user_cannot_turn_an_unconfirmed_transcript_into_learning_evidence(
        self,
    ) -> None:
        """Given a saved draft, when a response is requested before confirmation, then no turn or learning evidence is written."""
        before = self.evidence_counts()
        self.create_session()
        draft = self.store.save_draft(
            self.session_id,
            self.capability_hash,
            learner_id=self.learner_id,
            text="I think x is 37",
            source="speech_to_text",
            ambiguity_flags=["confidence_unavailable"],
        )

        with self.assertRaises(VoiceTranscriptNotConfirmed):
            self.store.claim_response(
                self.session_id,
                self.capability_hash,
                learner_id=self.learner_id,
                expected_generation=draft["generation"],
                transcript_revision=draft["draftTranscript"]["revision"],
                turn_id="turn-unconfirmed",
            )

        latest = self.store.get(
            self.session_id, self.capability_hash, learner_id=self.learner_id
        )
        self.assertIsNone(latest["confirmedTranscript"])
        self.assertEqual(latest["turns"], [])
        self.assertEqual(latest["generation"], 0)
        self.assertEqual(self.evidence_counts(), before)

    def test_user_keeps_transcription_diagnostics_when_confirming_unchanged_text(
        self,
    ) -> None:
        """Given a low-confidence transcript, when the same text is saved before confirmation, then its confidence warning remains visible."""
        self.create_session()
        first = self.store.save_draft(
            self.session_id,
            self.capability_hash,
            learner_id=self.learner_id,
            text="Why do we subtract 37?",
            source="speech_to_text",
            confidence={"kind": "token_logprobs", "lowConfidenceTokenCount": 1},
            ambiguity_flags=["model_low_confidence_tokens"],
        )

        latest = self.store.save_draft(
            self.session_id,
            self.capability_hash,
            learner_id=self.learner_id,
            text=first["draftTranscript"]["text"],
            source="speech_to_text",
        )

        self.assertEqual(
            latest["draftTranscript"]["confidence"],
            {"kind": "token_logprobs", "lowConfidenceTokenCount": 1},
        )
        self.assertEqual(
            latest["draftTranscript"]["ambiguityFlags"],
            ["model_low_confidence_tokens"],
        )
        self.assertTrue(latest["draftTranscript"]["needsConfirmation"])

    def test_user_can_interrupt_a_response_and_a_stale_generation_cannot_be_saved(
        self,
    ) -> None:
        """Given a confirmed draft and an active reply, when the learner cancels, then a late model result cannot append a turn."""
        self.create_session()
        draft = self.store.save_draft(
            self.session_id,
            self.capability_hash,
            learner_id=self.learner_id,
            text="Why do we subtract 37?",
            source="manual",
        )
        confirmed = self.store.confirm_transcript(
            self.session_id,
            self.capability_hash,
            learner_id=self.learner_id,
            revision=draft["draftTranscript"]["revision"],
            text="Why do we subtract 37?",
        )
        claim = self.store.claim_response(
            self.session_id,
            self.capability_hash,
            learner_id=self.learner_id,
            expected_generation=confirmed["generation"],
            transcript_revision=confirmed["confirmedTranscript"]["revision"],
            turn_id="turn-cancelled",
        )
        reconnect = VoiceStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(reconnect.close)
        cancelled = reconnect.cancel_response(
            self.session_id,
            self.capability_hash,
            learner_id=self.learner_id,
            expected_generation=claim["generation"],
        )

        late_turn = self.store.complete_response(
            self.session_id,
            self.capability_hash,
            learner_id=self.learner_id,
            generation=claim["generation"],
            turn_id="turn-cancelled",
            response="A stale reply must not appear.",
        )

        self.assertTrue(cancelled["cancelled"])
        self.assertIsNone(late_turn)
        self.assertEqual(cancelled["session"]["generation"], claim["generation"] + 1)
        self.assertFalse(cancelled["session"]["active"])
        self.assertEqual(cancelled["session"]["turns"], [])

    def test_user_editing_a_transcript_invalidates_the_old_response_generation(
        self,
    ) -> None:
        """Given a response for confirmed text, when the learner saves a correction, then the old response slot is invalidated."""
        self.create_session()
        draft = self.store.save_draft(
            self.session_id,
            self.capability_hash,
            learner_id=self.learner_id,
            text="Why do we subtract 36?",
            source="speech_to_text",
        )
        self.store.confirm_transcript(
            self.session_id,
            self.capability_hash,
            learner_id=self.learner_id,
            revision=draft["draftTranscript"]["revision"],
            text="Why do we subtract 36?",
        )
        claim = self.store.claim_response(
            self.session_id,
            self.capability_hash,
            learner_id=self.learner_id,
            expected_generation=0,
            transcript_revision=draft["draftTranscript"]["revision"],
            turn_id="turn-obsolete",
        )

        corrected = self.store.save_draft(
            self.session_id,
            self.capability_hash,
            learner_id=self.learner_id,
            text="Why do we subtract 37?",
            source="manual",
        )
        late_turn = self.store.complete_response(
            self.session_id,
            self.capability_hash,
            learner_id=self.learner_id,
            generation=claim["generation"],
            turn_id="turn-obsolete",
            response="This response used the old draft.",
        )

        self.assertIsNone(late_turn)
        self.assertEqual(corrected["generation"], claim["generation"] + 1)
        self.assertFalse(corrected["active"])
        self.assertIsNone(corrected["confirmedTranscript"])
        self.assertEqual(corrected["turns"], [])

    def test_user_can_reconnect_and_only_the_bound_learner_can_restore_the_session(
        self,
    ) -> None:
        """Given a session with a saved draft, when the API reconnects, then the same learner restores it and another learner sees no session."""
        self.create_session()
        self.store.save_draft(
            self.session_id,
            self.capability_hash,
            learner_id=self.learner_id,
            text="Please explain this step.",
            source="manual",
        )
        reconnected_store = VoiceStore(
            database_url=self.database_url, data_root=self.data_root
        )
        self.addCleanup(reconnected_store.close)

        restored = reconnected_store.get(
            self.session_id, self.capability_hash, learner_id=self.learner_id
        )
        self.assertEqual(
            restored["draftTranscript"]["text"], "Please explain this step."
        )
        with self.assertRaises(VoiceSessionNotFound):
            reconnected_store.get(
                self.session_id, self.capability_hash, learner_id="learner-voice-2"
            )

    def test_user_gets_only_one_response_claim_for_a_generation(self) -> None:
        """Given a confirmed transcript, when two requests race on the same generation, then only one owns the response slot."""
        self.create_session()
        draft = self.store.save_draft(
            self.session_id,
            self.capability_hash,
            learner_id=self.learner_id,
            text="Why is this operation valid?",
            source="manual",
        )
        self.store.confirm_transcript(
            self.session_id,
            self.capability_hash,
            learner_id=self.learner_id,
            revision=draft["draftTranscript"]["revision"],
            text="Why is this operation valid?",
        )
        second_store = VoiceStore(
            database_url=self.database_url, data_root=self.data_root
        )
        self.addCleanup(second_store.close)

        def claim(store: VoiceStore, turn_id: str):
            try:
                return store.claim_response(
                    self.session_id,
                    self.capability_hash,
                    learner_id=self.learner_id,
                    expected_generation=0,
                    transcript_revision=draft["draftTranscript"]["revision"],
                    turn_id=turn_id,
                )
            except VoiceSessionConflict:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda item: claim(*item),
                    [(self.store, "turn-a"), (second_store, "turn-b")],
                )
            )

        self.assertEqual(sum(result is not None for result in results), 1)
        latest = self.store.get(
            self.session_id, self.capability_hash, learner_id=self.learner_id
        )
        self.assertEqual(latest["generation"], 1)
        self.assertTrue(latest["active"])
