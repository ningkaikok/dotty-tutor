"""Small subject-profile registry for shared courseware orchestration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CoursewareSubjectProfile:
    subject_id: str
    plan_prompt_id: str
    page_prompt_id: str
    label: str


class CoursewareProfileUnavailable(ValueError):
    """The selected subject has no reviewed courseware profile."""


class CoursewareProfileRegistry:
    """Resolve only explicitly registered profiles; never infer math as fallback."""

    def __init__(self) -> None:
        self._profiles: dict[str, CoursewareSubjectProfile] = {}

    def register(self, profile: CoursewareSubjectProfile, *, replace: bool = False) -> None:
        if not profile.subject_id or len(profile.subject_id) > 32:
            raise ValueError("学科 profile ID 长度必须为 1–32")
        if profile.subject_id in self._profiles and not replace:
            raise ValueError(f"学科 profile 已注册：{profile.subject_id}")
        self._profiles[profile.subject_id] = profile

    def get(self, subject_id: str) -> CoursewareSubjectProfile:
        profile = self._profiles.get(subject_id)
        if profile is None:
            raise CoursewareProfileUnavailable(f"学科 {subject_id!r} 尚未配置课件生成规则")
        return profile

    def list_profiles(self) -> tuple[CoursewareSubjectProfile, ...]:
        """Return stable public choices without exposing the registry mapping."""
        return tuple(self._profiles[key] for key in sorted(self._profiles))

    def __contains__(self, subject_id: str) -> bool:
        return subject_id in self._profiles


courseware_profiles = CoursewareProfileRegistry()
courseware_profiles.register(CoursewareSubjectProfile(
    subject_id="english", plan_prompt_id="courseware.plan.english",
    page_prompt_id="courseware.page.english", label="English",
))
courseware_profiles.register(CoursewareSubjectProfile(
    subject_id="math", plan_prompt_id="courseware.plan.math",
    page_prompt_id="courseware.page.math", label="Mathematics",
))

courseware_profiles.register(CoursewareSubjectProfile(
    subject_id="science", plan_prompt_id="courseware.plan.science",
    page_prompt_id="courseware.page.science", label="科学（教师复核试点）",
))
