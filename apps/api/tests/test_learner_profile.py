from __future__ import annotations

import unittest

from application.services.learner_context import LearnerContextService
from domain.tutoring.learner_profile import build_learner_profile


class LearnerProfileTests(unittest.TestCase):
    def test_facts_are_scoped_expiring_and_evidence_backed(self) -> None:
        profile = build_learner_profile(
            now=100,
            mastery=[{"publicationId": "pub-a", "knowledgePointId": "kp-a", "score": 0.5, "evidenceRef": "attempt-1", "observedAt": 90}],
            confirmed_mistakes=[{"publicationId": "pub-a", "questionId": "q-a", "errorReason": "calculation", "mistakeId": "mistake-1", "confirmedAt": 95}],
            hint_dependencies=[{"publicationId": "pub-a", "questionId": "q-a", "hintLevel": 2, "evidenceRef": "turn-1", "observedAt": 99}],
            recent_practice=[{"publicationId": "pub-a", "questionId": "q-a", "assessment": "incorrect", "attemptId": "attempt-2", "createdAt": 99}],
        )
        self.assertEqual(len(profile.facts), 4)
        for fact in profile.facts:
            self.assertTrue(fact.evidence_ref)
            self.assertGreater(fact.expires_at, fact.observed_at)
            self.assertEqual(fact.profile_version, "learner-profile-v1")

    def test_stale_conflicting_and_unscoped_facts_are_excluded(self) -> None:
        profile = build_learner_profile(
            now=100,
            mastery=[
                {"publicationId": "pub-a", "knowledgePointId": "kp-a", "value": {"score": 0.1}, "evidenceRef": "old", "observedAt": 1, "expiresAt": 2},
                {"publicationId": "pub-a", "knowledgePointId": "kp-b", "value": {"score": 0.1}, "evidenceRef": "a", "observedAt": 90},
                {"publicationId": "pub-a", "knowledgePointId": "kp-b", "value": {"score": 0.9}, "evidenceRef": "b", "observedAt": 91},
                {"publicationId": "pub-b", "knowledgePointId": "kp-b", "value": {"score": 0.9}, "evidenceRef": "other-pub", "observedAt": 91},
            ],
            confirmed_mistakes=[{"questionId": "q-no-publication", "errorReason": "concept", "mistakeId": "m-1", "confirmedAt": 90}],
        )
        self.assertEqual(len(profile.facts), 1)
        self.assertEqual(profile.facts[0].publication_id, "pub-b")
        reasons = {item.reason for item in profile.excluded}
        self.assertIn("stale", reasons)
        self.assertIn("missing_publication_or_subject_scope", reasons)
        self.assertTrue(any(reason.startswith("conflict:") for reason in reasons))

    def test_forbidden_conversation_fields_never_enter_context(self) -> None:
        profile = build_learner_profile(
            now=100,
            recent_practice=[{"publicationId": "pub-a", "questionId": "q-a", "content": "完整聊天", "evidenceRef": "chat-1", "observedAt": 99}],
        )
        self.assertEqual(profile.facts, ())
        self.assertEqual(profile.excluded[0].reason, "forbidden_input_field:content")

    def test_context_service_is_shadow_only_even_when_flagged(self) -> None:
        result = LearnerContextService(enabled=True).build_shadow_context(
            now=100,
            mastery=[{"publicationId": "pub-a", "knowledgePointId": "kp-a", "score": 0.5, "evidenceRef": "a", "observedAt": 90}],
        )
        self.assertTrue(result["featureEnabled"])
        self.assertFalse(result["injected"])
        self.assertFalse(result["writesMastery"])
        self.assertFalse(result["changesSchedule"])
