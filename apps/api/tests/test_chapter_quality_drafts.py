"""User-visible source constraints for AI-authored chapter drafts."""
from __future__ import annotations

import unittest
from contextlib import contextmanager
from copy import deepcopy

from application.job_worker import RetryableJobError, TerminalJobError
from application.services.chapter_courses import ChapterCourseService, _strip_answers
from domain.chapters.quality import (
    ChapterDraftValidationError,
    build_quality_draft,
    quality_draft_schema,
)

REV = "revision-1"
MATH = {"sourceRevisionId": REV, "fingerprint": "source-hash", "pages": [{"page": 4, "text": "一次函数 y=2x+1。x 每增加 1，y 增加 2。", "regions": [], "sentences": [{"sentenceId": "s1", "text": "一次函数 y=2x+1。"}, {"sentenceId": "s2", "text": "x 每增加 1，y 增加 2。"}]}]}
ENGLISH = {"sourceRevisionId": REV, "pages": [{"page": 1, "text": "Unused cover page.", "regions": [], "sentences": [{"sentenceId": "cover", "text": "Unused cover page."}]}, {"page": 2, "text": "Mia moved to London.", "regions": [], "sentences": [{"sentenceId": "e1", "text": "Mia moved to London."}]}, {"page": 3, "text": "She likes the city because it has many parks.", "regions": [], "sentences": [{"sentenceId": "e2", "text": "She likes the city because it has many parks."}]}]}


def ref(sid: str, quote: str, page: int = 4) -> dict[str, object]:
    return {"sourceRevisionId": REV, "page": page, "sentenceId": sid, "quote": quote}


def math_draft() -> dict[str, object]:
    evidence = [ref("s2", "x 每增加 1，y 增加 2。")]
    return {"concept": {"text": "一次函数 y=2x+1。", "citations": [ref("s1", "一次函数 y=2x+1。")]},
            "conditions": {"text": "x 每增加 1，y 增加 2。", "citations": evidence},
            "example": {"prompt": "x 增加 1 时 y 如何变化？", "answer": "增加 2", "steps": ["查看变化量"], "citations": evidence},
            "hints": [{"text": "找 x", "citations": evidence}, {"text": "找 y", "citations": evidence}, {"text": "对应变化", "citations": evidence}],
            "check": {"prompt": "y 增加多少？", "answer": "2", "citations": evidence}}


class ChapterQualityDraftBehaviorTests(unittest.TestCase):
    def test_user_checks_the_generation_contract_then_model_must_return_complete_strict_shapes(self) -> None:
        # Given a subject-specific model generation contract
        math_schema = quality_draft_schema("math")
        english_schema = quality_draft_schema("english")
        # When the Worker passes it to ModelRuntime
        # Then every generated section has a closed, fully required shape
        self.assertEqual(math_schema["additionalProperties"], False)
        self.assertEqual(math_schema["required"], ["concept", "conditions", "example", "hints", "check"])
        self.assertEqual(math_schema["properties"]["hints"]["minItems"], 3)
        self.assertEqual(english_schema["properties"]["questions"]["minItems"], 4)
        self.assertEqual(english_schema["properties"]["questions"]["items"]["properties"]["kind"]["enum"], ["explicit", "inference", "reference", "word_meaning"])

    def test_user_generates_math_material_then_each_learning_step_is_source_bound_and_reviewable(self) -> None:
        # Given source-backed concepts, conditions, an example, three hints and a check
        draft = {
            "concept": {"text": "一次函数可表示为 y=2x+1。", "citations": [ref("s1", "一次函数 y=2x+1。")]},
            "conditions": {"text": "x 每增加 1，y 增加 2。", "citations": [ref("s2", "x 每增加 1，y 增加 2。")]},
            "example": {"prompt": "x 增加 1 时 y 如何变化？", "answer": "增加 2", "steps": ["找出 x 的变化", "读取 y 的变化"], "citations": [ref("s2", "x 每增加 1，y 增加 2。")]},
            "hints": [{"text": "找 x 的变化", "citations": [ref("s2", "x 每增加 1，y 增加 2。")]}, {"text": "找对应的 y 变化", "citations": [ref("s2", "x 每增加 1，y 增加 2。")]}, {"text": "说明两者关系", "citations": [ref("s2", "x 每增加 1，y 增加 2。")]}],
            "check": {"prompt": "x 每增加 1，y 增加多少？", "answer": "2", "citations": [ref("s2", "x 每增加 1，y 增加 2。")]},
        }
        # When the source-grounded draft is materialized
        lessons, issues = build_quality_draft({"chapterId": "c1", "title": "一次函数", "subject": "math", "version": 1}, MATH, draft, lesson_id_factory=lambda: "lesson-1")
        # Then it stays an explicitly unapproved review draft
        lesson = lessons[0]
        self.assertEqual(issues, [])
        self.assertEqual(lesson["status"], "in_review")
        self.assertEqual(lesson["questionPayload"]["quality"]["status"], "needs_review")
        self.assertEqual(lesson["questionPayload"]["question"]["evaluation"]["mode"], "tutor")
        self.assertTrue(all(block["payload"].get("sourceRefs") for block in lesson["blocks"][:2]))
        self.assertTrue(all(block["payload"].get("sourceRefs") for block in lesson["blocks"][2:5]))

    def test_user_receives_forged_source_quote_then_generation_is_rejected(self) -> None:
        # Given an otherwise valid draft whose cited sentence exists
        chapter = {"chapterId": "c1", "title": "一次函数", "subject": "math", "version": 1}
        build_quality_draft(chapter, MATH, math_draft())
        # When only the quote is invented or empty
        for quote in ("来源没有写过的话", ""):
            draft = math_draft()
            draft["concept"]["citations"][0]["quote"] = quote
            # Then citation validation rejects it, independently of other fields
            with self.subTest(quote=quote), self.assertRaises(ChapterDraftValidationError):
                build_quality_draft(chapter, MATH, draft)

    def test_user_generates_english_practice_then_four_question_kinds_and_variants_need_review(self) -> None:
        # Given a reading passage and four supported question types
        citation = {"sourceRevisionId": REV, "page": 2, "sentenceId": "e1", "quote": "Mia moved to London."}
        items = []
        for kind in ("word_meaning", "reference", "explicit", "inference"):
            items.append({"kind": kind, "prompt": f"Question for {kind}?", "answer": "London", "citations": [citation, {"sourceRevisionId": REV, "page": 3, "sentenceId": "e2", "quote": "She likes the city because it has many parks."}], "teacherVariants": ["to London"], "rubric": ["基于原文作答"]})
        # When the draft is materialized
        lessons, issues = build_quality_draft({"chapterId": "c2", "title": "Reading", "subject": "english", "version": 1}, ENGLISH, {"questions": items}, lesson_id_factory=lambda: "lesson-en")
        # Then all kinds are present while suggested variants remain unapproved
        self.assertEqual(issues, [])
        questions = [lesson["questionPayload"]["question"] for lesson in lessons]
        self.assertEqual({item["questionKind"] for item in questions}, {"word_meaning", "reference", "explicit", "inference"})
        self.assertTrue(all(item["questionType"] == "short-answer" and item["subject"] == "english" for item in questions))
        self.assertTrue(all(item["variantReviewStatus"] == "needs_teacher_review" for item in questions))
        self.assertTrue(all(lesson["sourceLocator"]["page"] == 2 for lesson in lessons))
        self.assertTrue(all(lesson["status"] == "in_review" for lesson in lessons))
        self.assertEqual([block["payload"]["markdown"] for block in lessons[0]["blocks"] if block["type"] == "markdown"],
                         ["Mia moved to London.", "She likes the city because it has many parks."])
        lesson = lessons[0]
        stored = _FakeChapterStore({"chapterId": "c2", "subject": "english", "title": "Reading", "recordVersion": 1,
                                    "sourceRevisions": [ENGLISH], "lessons": [{"lessonId": lesson["lessonId"], "sourceRevisionId": REV, "sourceLocator": lesson["sourceLocator"]}],
                                    "currentLessonIds": [lesson["lessonId"]], "reviewIssues": [], "publications": []})
        stored.save_lesson(lesson)
        reviewed = ChapterCourseService(stored).get("c2")["lessons"][0]
        self.assertEqual({option["page"] for option in reviewed["evidenceOptions"]}, {2, 3})

    def test_user_publishes_a_reviewed_lesson_then_check_answers_are_removed_from_quiz_blocks(self) -> None:
        # Given a quiz block with authoring aids and an annotation with a worked answer
        blocks = [{"type": "quiz", "payload": {"questionId": "lesson", "answerDraft": "2", "sourceRefs": [{"page": 4}], "teacherVariants": ["two"]}},
                  {"type": "annotation", "payload": {"text": "示例解答：2"}}]
        # When public blocks are projected
        public = _strip_answers(blocks)
        # Then the prompt identity stays while grading answers/evidence are removed
        self.assertEqual(public[0]["payload"], {"questionId": "lesson"})
        self.assertEqual(public[1]["payload"]["text"], "示例解答：2")

    def test_user_omits_math_conditions_or_english_question_kinds_then_generation_fails_closed(self) -> None:
        # Given incomplete subject-specific drafts
        math = {"concept": {"text": "概念", "citations": [ref("s1", "一次函数 y=2x+1。")]}, "conditions": {"text": "", "citations": []}, "example": {"prompt": "Q", "answer": "A", "steps": ["step"], "citations": [ref("s2", "x 每增加 1，y 增加 2。")]}, "hints": [], "check": {"prompt": "Q", "answer": "A", "citations": [ref("s2", "x 每增加 1，y 增加 2。")]}}
        # When each incomplete shape reaches validation
        with self.assertRaises(ChapterDraftValidationError):
            build_quality_draft({"chapterId": "c", "title": "T", "subject": "math", "version": 1}, MATH, math)
        with self.assertRaises(ChapterDraftValidationError):
            build_quality_draft({"chapterId": "c", "title": "T", "subject": "english", "version": 1}, ENGLISH, {"questions": []})

    def test_user_waits_for_background_generation_then_source_version_and_unapproved_draft_are_saved(self) -> None:
        # Given an unchanged chapter and a model runtime that asserts no chapter lock is held
        chapter = {"chapterId": "c1", "title": "一次函数", "subject": "math", "version": 1, "recordVersion": 3,
                   "sourceRevisions": [{**MATH, "issues": []}], "lessons": [], "currentLessonIds": [],
                   "reviewIssues": [], "publications": [], "publicationId": None}
        store = _FakeChapterStore(chapter)
        runtime = _FakeChapterRuntime(store, math_draft())
        service = ChapterCourseService(store, generation_runtime=runtime)
        payload = {"chapterId": "c1", "sourceRevisionId": REV, "expectedRecordVersion": 3,
                   "runtimeSnapshot": {"version": 1, "generation": {"provider": "codex", "model": "default"}, "ocr": {"provider": "mineru"}, "review": {"provider": "codex", "model": "default"}}}

        # When the Worker handler finishes
        execution = service.run_ai_generation(payload)
        result = execution.value

        # Then lessons are linked to the pinned source and remain in human review
        self.assertFalse(runtime.called_while_locked)
        self.assertEqual(result["status"], "in_review")
        self.assertTrue(result["humanReviewRequired"])
        self.assertEqual(store.chapter["recordVersion"], 4)
        self.assertEqual(store.chapter["currentLessonIds"], result["lessonIds"])
        audit = next(iter(store.chapter["aiGenerationRuns"].values()))["audit"]
        self.assertEqual(audit["sourceFingerprint"], MATH.get("fingerprint"))
        self.assertTrue(audit["promptSha256"])
        self.assertTrue(audit["schemaSha256"])
        self.assertEqual(audit["runtime"]["provider"], "codex")
        self.assertEqual(runtime.selected_provider, "codex")
        self.assertNotIn("aiGenerationRuns", service.get("c1"))
        retried = service.run_ai_generation(payload)
        self.assertEqual(retried, result)
        self.assertEqual(runtime.calls, 1)
        edited = service.edit_lesson("c1", result["lessonIds"][0], {
            "prompt": "y 增加多少？", "answer": "2", "answerType": "text", "questionKind": "short_answer",
            "answerMode": "short_answer", "acceptedAnswers": ["2"],
            "requiredEvidenceRefs": [ref("s2", "x 每增加 1，y 增加 2。")], "rubric": {},
            "conceptMarkdown": "概念与适用条件", "exampleText": "例题解答", "hint": "第一级已核对",
            "hints": ["提示一已核对", "提示二已核对", "提示三已核对"],
            "sourceRevisionId": REV, "page": 4, "expectedRecordVersion": 4,
        })
        hint_blocks = [block for block in edited["lessons"][0]["blocks"] if block["type"] == "hint"]
        self.assertEqual([block["payload"]["hint"] for block in hint_blocks], ["提示一已核对", "提示二已核对", "提示三已核对"])
        self.assertTrue(all(block["payload"].get("sourceRefs") for block in hint_blocks))
        self.assertEqual(edited["lessons"][0]["questionPayload"]["quality"]["status"], "needs_review")
        approved = service.review("c1", result["lessonIds"][0], "approve", "teacher", expected_record_version=5)
        self.assertEqual(approved["lessons"][0]["questionPayload"]["quality"]["status"], "ready")
        self.assertEqual(approved["lessons"][0]["questionPayload"]["quality"]["reviewBasis"], "teacher_approved_ai_draft")

    def test_user_generates_with_deepseek_then_the_pinned_draft_can_be_reviewed(self) -> None:
        # Given a deployment job pinned to DeepSeek at the external model boundary
        chapter = {"chapterId": "c1", "title": "一次函数", "subject": "math", "version": 1, "recordVersion": 3,
                   "sourceRevisions": [{**MATH, "issues": []}], "lessons": [], "currentLessonIds": [],
                   "reviewIssues": [], "publications": [], "publicationId": None}
        store = _FakeChapterStore(chapter)
        runtime = _FakeChapterRuntime(store, math_draft())
        service = ChapterCourseService(store, generation_runtime=runtime)
        payload = {"chapterId": "c1", "sourceRevisionId": REV, "expectedRecordVersion": 3,
                   "runtimeSnapshot": {"version": 1, "generation": {"provider": "deepseek", "model": "deepseek-flash"},
                                       "ocr": {"provider": "mineru"}, "review": {"provider": "deepseek", "model": "deepseek-flash"}}}
        # When the worker produces a source-bound draft
        result = service.run_ai_generation(payload).value
        # Then it remains reviewable and keeps its deployment provider identity
        self.assertTrue(result["humanReviewRequired"])
        self.assertTrue(store.chapter["currentLessonIds"])
        audit = next(iter(store.chapter["aiGenerationRuns"].values()))["audit"]
        self.assertEqual(audit["runtime"]["provider"], "deepseek")

    def test_user_cancels_after_generation_write_then_only_that_unreviewed_result_is_compensated(self) -> None:
        # Given a completed model result whose Worker has not converged yet
        chapter = {"chapterId": "c1", "title": "一次函数", "subject": "math", "version": 1, "recordVersion": 3,
                   "sourceRevisions": [{**MATH, "issues": []}], "lessons": [], "currentLessonIds": [],
                   "reviewIssues": [], "publications": [], "publicationId": None}
        store = _FakeChapterStore(chapter)
        runtime = _FakeChapterRuntime(store, math_draft())
        service = ChapterCourseService(store, generation_runtime=runtime)
        payload = {"chapterId": "c1", "sourceRevisionId": REV, "expectedRecordVersion": 3,
                   "runtimeSnapshot": {"version": 1, "generation": {"provider": "codex", "model": "default"}, "ocr": {"provider": "mineru"}, "review": {"provider": "codex", "model": "default"}}}

        # When cancellation wins before the Worker commits the job result
        execution = service.run_ai_generation(payload)
        execution.on_cancel()

        # Then the unreviewed generation is removed and its lessons are archived
        self.assertEqual(store.chapter["currentLessonIds"], [])
        self.assertEqual(store.chapter.get("aiGenerationRuns"), {})
        self.assertTrue(all(item["status"] == "archived" for item in store.lessons.values()))

    def test_user_edits_during_model_generation_then_stale_result_is_not_written(self) -> None:
        # Given a model call that races with a teacher edit
        chapter = {"chapterId": "c1", "title": "一次函数", "subject": "math", "version": 1, "recordVersion": 3,
                   "sourceRevisions": [{**MATH, "issues": []}], "lessons": [], "currentLessonIds": [],
                   "reviewIssues": [], "publications": [], "publicationId": None}
        store = _FakeChapterStore(chapter)
        runtime = _FakeChapterRuntime(store, math_draft())
        runtime.edit_during_call = True
        service = ChapterCourseService(store, generation_runtime=runtime)
        payload = {"chapterId": "c1", "sourceRevisionId": REV, "expectedRecordVersion": 3,
                   "runtimeSnapshot": {"version": 1, "generation": {"provider": "codex", "model": "default"}, "ocr": {"provider": "mineru"}, "review": {"provider": "codex", "model": "default"}}}

        # When the generated result tries to finish against its stale version
        with self.assertRaises(TerminalJobError):
            service.run_ai_generation(payload)

        # Then the stale draft cannot replace the teacher's newer record
        self.assertEqual(store.saved_lessons, [])
        self.assertEqual(store.chapter["recordVersion"], 4)

    def test_user_retries_after_provider_error_then_job_diagnostics_keep_only_safe_runtime_fields(self) -> None:
        # Given an external provider error that includes sensitive text and unsafe diagnostic metadata
        chapter = {"chapterId": "c1", "title": "一次函数", "subject": "math", "version": 1, "recordVersion": 3,
                   "sourceRevisions": [{**MATH, "issues": []}], "lessons": [], "currentLessonIds": [],
                   "reviewIssues": [], "publications": [], "publicationId": None}
        store = _FakeChapterStore(chapter)
        runtime = _FakeChapterRuntime(store, math_draft())
        runtime.failure = OSError("api_key=do-not-log")
        runtime.failure.runtime_run = {"provider": "codex", "model": "default", "promptChars": 20, "apiKey": "do-not-log"}
        service = ChapterCourseService(store, generation_runtime=runtime)
        payload = {"chapterId": "c1", "sourceRevisionId": REV, "expectedRecordVersion": 3,
                   "runtimeSnapshot": {"version": 1, "generation": {"provider": "codex", "model": "default"}, "ocr": {"provider": "mineru"}, "review": {"provider": "codex", "model": "default"}}}

        # When generation fails at the model boundary
        with self.assertRaises(RetryableJobError) as caught:
            service.run_ai_generation(payload)

        # Then retry metadata is actionable without exposing provider secrets
        self.assertNotIn("do-not-log", str(caught.exception))
        self.assertEqual(caught.exception.details["runtime"], {"provider": "codex", "model": "default", "promptChars": 20})
        self.assertTrue(caught.exception.details["promptSha256"])
        self.assertTrue(caught.exception.details["schemaSha256"])


class _FakeChapterStore:
    def __init__(self, chapter: dict[str, object]) -> None:
        self.chapter = deepcopy(chapter)
        self.saved_lessons: list[dict[str, object]] = []
        self.lessons: dict[str, dict[str, object]] = {}
        self.locked = False

    def load_chapter(self, chapter_id: str) -> dict[str, object] | None:
        return deepcopy(self.chapter) if chapter_id == self.chapter["chapterId"] else None

    @contextmanager
    def chapter_lock(self, _chapter_id: str):
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    @contextmanager
    def atomic(self):
        yield

    def save_lesson(self, lesson: dict[str, object]) -> dict[str, object]:
        saved = deepcopy(lesson)
        self.lessons[str(lesson["lessonId"])] = saved
        self.saved_lessons.append(saved)
        return lesson

    def load_lesson(self, lesson_id: str) -> dict[str, object] | None:
        lesson = self.lessons.get(lesson_id)
        return deepcopy(lesson) if lesson else None

    def save_chapter(self, chapter: dict[str, object]) -> dict[str, object]:
        chapter["recordVersion"] = int(chapter["recordVersion"]) + 1
        self.chapter = deepcopy(chapter)
        return chapter


class _FakeChapterRuntime:
    def __init__(self, store: _FakeChapterStore, draft: dict[str, object]) -> None:
        self.store = store
        self.draft = draft
        self.called_while_locked = False
        self.edit_during_call = False
        self.calls = 0
        self.selected_provider = ""
        self.failure: Exception | None = None

    def generate_json(self, *_args: object, **_kwargs: object) -> tuple[dict[str, object], dict[str, object]]:
        self.calls += 1
        self.called_while_locked = self.store.locked
        selection = _kwargs.get("selection")
        self.selected_provider = str(getattr(selection, "provider", ""))
        if self.failure:
            raise self.failure
        if self.edit_during_call:
            self.store.chapter["recordVersion"] = int(self.store.chapter["recordVersion"]) + 1
        return self.draft, {"provider": self.selected_provider, "model": "default", "promptChars": 100, "maxOutputTokens": 2400, "durationMs": 1.0}


if __name__ == "__main__":
    unittest.main()
