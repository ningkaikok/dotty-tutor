import { describe, expect, it } from "vitest";
import {
  enqueuePendingAttempt,
  LEGACY_OFFLINE_ATTEMPT_QUEUE_KEY,
  OfflineAttemptQueueStorageError,
  OFFLINE_ATTEMPT_QUEUE_KEY,
  QUARANTINED_OFFLINE_ATTEMPT_QUEUE_KEY,
  readPendingAttempts,
  removePendingAttempts,
  type StorageLike,
} from "./offlineAttemptQueue";
import type { ExerciseAttemptInput } from "../../types/index";

class MemoryStorage implements StorageLike {
  private values = new Map<string, string>();

  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.values.set(key, value); }
  removeItem(key: string) { this.values.delete(key); }
}

const attempt = (attemptId: string): ExerciseAttemptInput => ({
  attemptId,
  questionId: "question-1",
  response: { text: "答案" },
  assessment: "incorrect",
  hintLevel: 0,
  durationMs: 300,
  createdAt: 1,
});

const scope = (sessionId: string, publicationId = "publication-1", learnerId = "learner-1") => ({
  learnerId,
  publicationId,
  sessionId,
});

describe("offline attempt queue", () => {
  it("isolates attempts by learner, publication, and session while keeping attemptId idempotent", () => {
    const storage = new MemoryStorage();
    enqueuePendingAttempt(scope("session-1"), attempt("attempt-1"), { storage });
    enqueuePendingAttempt(scope("session-1"), attempt("attempt-1"), { storage });
    enqueuePendingAttempt(scope("session-2"), attempt("attempt-2"), { storage });
    enqueuePendingAttempt(scope("session-1", "publication-2"), attempt("attempt-3"), { storage });

    expect(readPendingAttempts(scope("session-1"), { storage }).map((item) => item.attempt.attemptId))
      .toEqual(["attempt-1"]);
    expect(readPendingAttempts(scope("session-2"), { storage }).map((item) => item.attempt.attemptId))
      .toEqual(["attempt-2"]);
    expect(readPendingAttempts(scope("session-1", "publication-2"), { storage }).map((item) => item.attempt.attemptId))
      .toEqual(["attempt-3"]);
  });

  it("migrates only legacy records with the current or replaced session", () => {
    const storage = new MemoryStorage();
    storage.setItem(LEGACY_OFFLINE_ATTEMPT_QUEUE_KEY, JSON.stringify([
      { sessionId: "new-session", attempt: attempt("legacy-current") },
      { sessionId: "old-session", attempt: attempt("legacy-1") },
      { sessionId: "other-session", attempt: attempt("legacy-2") },
      { sessionId: "", attempt: attempt("legacy-3") },
    ]));

    const current = readPendingAttempts(scope("new-session"), {
      replacedSessionId: "old-session",
      storage,
    });
    expect(current.map((item) => item.attempt.attemptId)).toEqual(["legacy-current", "legacy-1"]);
    expect(current.every((item) => item.sessionId === "new-session")).toBe(true);
    expect(JSON.parse(storage.getItem(OFFLINE_ATTEMPT_QUEUE_KEY) ?? "[]")).toHaveLength(2);
    expect(JSON.parse(storage.getItem(LEGACY_OFFLINE_ATTEMPT_QUEUE_KEY) ?? "[]")).toHaveLength(0);
    expect(JSON.parse(storage.getItem(QUARANTINED_OFFLINE_ATTEMPT_QUEUE_KEY) ?? "[]").map((item: { record: { attempt?: ExerciseAttemptInput } }) => item.record.attempt?.attemptId))
      .toEqual(["legacy-2", "legacy-3"]);
  });

  it("does not assign an unbound legacy record when learner or publication changes", () => {
    const storage = new MemoryStorage();
    storage.setItem(LEGACY_OFFLINE_ATTEMPT_QUEUE_KEY, JSON.stringify([
      { attempt: attempt("unbound-1") },
    ]));

    expect(readPendingAttempts(scope("session-1", "publication-1", "learner-1"), { storage })).toEqual([]);
    expect(readPendingAttempts(scope("session-2", "publication-2", "learner-2"), { storage })).toEqual([]);
    expect(JSON.parse(storage.getItem(OFFLINE_ATTEMPT_QUEUE_KEY) ?? "[]")).toEqual([]);
    expect(JSON.parse(storage.getItem(QUARANTINED_OFFLINE_ATTEMPT_QUEUE_KEY) ?? "[]")).toHaveLength(1);
  });

  it("removes only delivered attempts from the matching scope", () => {
    const storage = new MemoryStorage();
    enqueuePendingAttempt(scope("session-1"), attempt("attempt-1"), { storage });
    enqueuePendingAttempt(scope("session-2"), attempt("attempt-1"), { storage });

    removePendingAttempts(scope("session-1"), ["attempt-1"], { storage });
    expect(readPendingAttempts(scope("session-1"), { storage })).toEqual([]);
    expect(readPendingAttempts(scope("session-2"), { storage })).toHaveLength(1);
  });

  it("reports unavailable storage instead of silently dropping an attempt", () => {
    const unavailable: StorageLike = {
      getItem: () => { throw new Error("blocked"); },
      setItem: () => { throw new Error("blocked"); },
      removeItem: () => { throw new Error("blocked"); },
    };
    expect(() => enqueuePendingAttempt(scope("session-1"), attempt("attempt-1"), { storage: unavailable }))
      .toThrowError(OfflineAttemptQueueStorageError);
  });
});
