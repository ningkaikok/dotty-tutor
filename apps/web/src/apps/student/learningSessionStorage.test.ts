import { describe, expect, it } from "vitest";
import {
  isLearningSessionForScope,
  learningSessionStorageKey,
} from "./learningSessionStorage";

describe("learner-scoped learning session storage", () => {
  const scope = { learnerId: "stu/001", publicationId: "paper-1", assignmentId: "assignment-1" };

  it("includes learner and activity scope in the localStorage key", () => {
    const key = learningSessionStorageKey(scope);

    expect(key).toBe("dotty-learning-session:stu%2F001:assignment-1");
    expect(learningSessionStorageKey({ ...scope, learnerId: "stu-002" })).not.toBe(key);
    expect(learningSessionStorageKey({ ...scope, publicationId: "paper-2", assignmentId: undefined })).not.toBe(key);
  });

  it("rejects a restored session from another learner or activity", () => {
    const session = {
      sessionId: "session-1",
      learnerId: "stu-001",
      publicationId: "paper-1",
      assignmentId: "assignment-1",
    };

    expect(isLearningSessionForScope(session, { ...scope, learnerId: "stu-001" })).toBe(true);
    expect(isLearningSessionForScope(session, { ...scope, learnerId: "stu-002" })).toBe(false);
    expect(isLearningSessionForScope(session, { ...scope, assignmentId: "assignment-2" })).toBe(false);
  });
});
