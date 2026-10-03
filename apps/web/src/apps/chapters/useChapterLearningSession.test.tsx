// @vitest-environment jsdom

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetLearnerIdCacheForTests, setCurrentLearnerId, setProtectedSession } from "../../api/identity";
import type { PublishedChapter } from "../../types/chapter";
import { useChapterLearningSession } from "./useChapterLearningSession";

const api = vi.hoisted(() => ({
  loadChapterAttempt: vi.fn(),
  submitChapterAttempt: vi.fn(),
}));

vi.mock("../../api/chapters", () => api);

const chapter: PublishedChapter = {
  chapterId: "chapter-1",
  subject: "english",
  title: "A day outdoors",
  publicationId: "publication-1",
  version: 1,
  status: "published",
  lessons: [{
    lessonId: "lesson-1",
    title: "Read",
    version: 1,
    status: "published",
    knowledgePoints: [],
    blocks: [],
    sourceRevisionId: "source-1",
    sourceLocator: { sourceRevisionId: "source-1", page: 4, regions: [] },
    reviewIssues: [],
    evidenceOptions: [{ sourceRevisionId: "source-1", page: 4, sentenceId: "sentence-1", quote: "They went to the park.", label: "They went to the park." }],
    questionPayload: { question: { id: "question-1", prompt: "Where did they go?", questionType: "short-answer" } },
  }],
};

describe("useChapterLearningSession identity scope", () => {
  beforeEach(() => {
    localStorage.clear();
    setProtectedSession(null);
    resetLearnerIdCacheForTests();
    setCurrentLearnerId("student-a");
    api.loadChapterAttempt.mockReset();
    api.submitChapterAttempt.mockReset();
  });

  afterEach(() => {
    cleanup();
    setProtectedSession(null);
    resetLearnerIdCacheForTests();
  });

  it("user Given a learner has saved an answer When another learner signs in Then the prior answer and feedback stay isolated", async () => {
    const { result } = renderHook(() => useChapterLearningSession(chapter));
    act(() => result.current.changeAnswer("The park"));
    await waitFor(() => expect(localStorage.getItem("dotty-chapter-session:student-a:chapter-1:publication-1")).toContain("The park"));

    act(() => setProtectedSession({ role: "student", learnerId: "student-b", expiresAt: Date.now() + 60_000 }));

    await waitFor(() => expect(result.current.draft.answer).toBe(""));
    expect(localStorage.getItem("dotty-chapter-session:student-b:chapter-1:publication-1")).not.toContain("The park");
  });

  it("user Given learner A submits an answer When the account changes before the server replies Then the old result is not attached to learner B", async () => {
    let finishSubmission: ((value: unknown) => void) | undefined;
    api.submitChapterAttempt.mockImplementation(() => new Promise((resolve) => { finishSubmission = resolve; }));
    const { result } = renderHook(() => useChapterLearningSession(chapter));
    act(() => result.current.changeAnswer("the park"));
    act(() => result.current.changeEvidence([{ sourceRevisionId: "source-1", page: 4, sentenceId: "sentence-1", quote: "They went to the park." }]));
    act(() => { void result.current.submit(); });
    await waitFor(() => expect(localStorage.getItem("dotty-chapter-session:student-a:chapter-1:publication-1")).toContain("attemptId"));

    act(() => setProtectedSession({ role: "student", learnerId: "student-b", expiresAt: Date.now() + 60_000 }));
    await waitFor(() => expect(result.current.draft.answer).toBe(""));
    await act(async () => finishSubmission?.({
      attemptId: "attempt-a", publicationId: "publication-1", lessonId: "lesson-1", learnerId: "student-a",
      answer: { text: "the park" }, evidenceRefs: [], assessment: "correct", evidenceVerdict: "supported", feedback: { message: "正确" },
    }));

    expect(result.current.draft.result).toBeUndefined();
    expect(localStorage.getItem("dotty-chapter-session:student-b:chapter-1:publication-1")).not.toContain("attempt-a");
  });

  it.each([
    ["another learner", { learnerId: "student-b", publicationId: "publication-1", lessonId: "lesson-1" }],
    ["another publication", { learnerId: "student-a", publicationId: "publication-2", lessonId: "lesson-1" }],
    ["another lesson", { learnerId: "student-a", publicationId: "publication-1", lessonId: "lesson-2" }],
  ])("user Given a saved attempt is returned for %s When the page restores Then feedback from a different scope stays hidden", async (_scope, responseScope) => {
    localStorage.setItem("dotty-chapter-session:student-a:chapter-1:publication-1", JSON.stringify({
      lessonIndex: 0,
      drafts: { "lesson-1": { answer: "the park", evidenceRefs: [], attemptId: "attempt-a", result: { attemptId: "attempt-a", assessment: "correct", evidenceVerdict: "supported", feedback: { message: "cached" } } } },
    }));
    api.loadChapterAttempt.mockResolvedValue({
      attemptId: "attempt-a", publicationId: responseScope.publicationId, lessonId: responseScope.lessonId, learnerId: responseScope.learnerId,
      answer: { text: "the park" }, evidenceRefs: [], assessment: "correct", evidenceVerdict: "supported", feedback: { message: "wrong scope" },
    });
    const { result } = renderHook(() => useChapterLearningSession(chapter));

    await waitFor(() => expect(result.current.draft.answer).toBe("the park"));

    expect(result.current.draft.result).toBeUndefined();
  });
});
