"""Feature-flagged shadow context assembly for learner-profile experiments."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from domain.tutoring.learner_profile import LearnerProfile, build_learner_profile


class LearnerContextService:
    """Build profile context without injecting it into the production Tutor."""

    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled

    def build_shadow_context(
        self,
        *,
        mastery: Iterable[Mapping[str, Any]] = (),
        confirmed_mistakes: Iterable[Mapping[str, Any]] = (),
        hint_dependencies: Iterable[Mapping[str, Any]] = (),
        recent_practice: Iterable[Mapping[str, Any]] = (),
        now: float,
    ) -> dict[str, Any]:
        profile: LearnerProfile = build_learner_profile(
            mastery=mastery,
            confirmed_mistakes=confirmed_mistakes,
            hint_dependencies=hint_dependencies,
            recent_practice=recent_practice,
            now=now,
        )
        return {
            "featureEnabled": self.enabled,
            "mode": "shadow",
            "injected": False,
            "profile": profile.as_dict(),
            "shadowContext": profile.context() if self.enabled else None,
            "writesMastery": False,
            "changesSchedule": False,
        }
