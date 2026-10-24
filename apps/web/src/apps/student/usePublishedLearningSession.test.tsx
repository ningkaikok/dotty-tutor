// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ExerciseAttemptResult } from "../../api/learning";

const { createSessionMock, loadSessionMock, loadMasteryMock, recordAttemptMock, syncAttemptsMock } = vi.hoisted(() => ({
  createSessionMock: vi.fn(),
  loadSessionMock: vi.fn(),
  loadMasteryMock: vi.fn(),
  recordAttemptMock: vi.fn(),
  syncAttemptsMock: vi.fn(),
}));

vi.mock("../../api/learning", () => ({
  createLearningSession: createSessionMock,
  loadLearningMastery: loadMasteryMock,
  loadLearningSession: loadSessionMock,
  recordExerciseAttempt: recordAttemptMock,
  syncExerciseAttempts: syncAttemptsMock,
}));

import { resetLearnerIdCacheForTests, setCurrentLearnerId } from "../../api/identity";
import { usePublishedLearningSession } from "./usePublishedLearningSession";

describe("user: 已发布练习的晚返回判题结果始终留在原学生范围", () => {
  beforeEach(() => {
    localStorage.clear();
    resetLearnerIdCacheForTests();
    setCurrentLearnerId("learner-a");
    createSessionMock.mockImplementation(async ({ learnerId, publicationId, assignmentId }) => ({
      sessionId: `session-${learnerId}`,
      learnerId,
      publicationId,
      assignmentId,
      startedAt: 1,
      attempts: [],
    }));
    loadSessionMock.mockResolvedValue({});
    loadMasteryMock.mockResolvedValue([]);
    syncAttemptsMock.mockResolvedValue({ synced: [] });
    recordAttemptMock.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    localStorage.clear();
    resetLearnerIdCacheForTests();
  });

  it("Given 甲的作答判定仍在网络中 When 切换到乙后判定才返回 Then 乙的掌握度不会混入甲的响应", async () => {
    let finishOldRequest!: (result: ExerciseAttemptResult) => void;
    recordAttemptMock.mockImplementation(() => new Promise((resolve) => { finishOldRequest = resolve; }));
    const { result } = renderHook(() => usePublishedLearningSession("paper-1"));
    await waitFor(() => expect(result.current.sessionReady).toBe(true));

    let queued!: Promise<{ status: "saved" | "queued" }>;
    act(() => {
      queued = result.current.queueAttempt({
        attemptId: "attempt-a",
        questionId: "question-1",
        response: { text: "答案" },
        assessment: "correct",
        hintLevel: 0,
        durationMs: 100,
        createdAt: 1,
      });
    });
    await waitFor(() => expect(recordAttemptMock).toHaveBeenCalledTimes(1));

    act(() => setCurrentLearnerId("learner-b"));
    await waitFor(() => expect(createSessionMock).toHaveBeenCalledWith(expect.objectContaining({ learnerId: "learner-b" })));
    await waitFor(() => expect(result.current.sessionReady).toBe(true));
    expect(result.current.mastery).toEqual([]);

    await act(async () => {
      finishOldRequest({
        attemptId: "attempt-a",
        mastery: {
          learnerId: "learner-a",
          knowledgePointId: "kp-a",
          knowledgePoint: "甲的知识点",
          score: 80,
          rawScore: 80,
          evidenceConfidence: 1,
          evidenceCount: 1,
          algorithmVersion: "test",
          computedAt: 1,
          attemptCount: 1,
          correctCount: 1,
          lastPracticedAt: 1,
        },
      });
      await queued;
    });

    expect(result.current.mastery).toEqual([]);
  });
});
