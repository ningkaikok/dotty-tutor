import type { LearningSession, MasteryState } from "../types/lesson";
import { parse } from "./client";

export async function createLearningSession(input: { learnerId: string; lessonId: string }): Promise<LearningSession> {
  return parse<LearningSession>(await fetch("/api/learning/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function recordExerciseAttempt(sessionId: string, input: {
  questionId: string;
  knowledgePoint: string;
  response: Record<string, unknown>;
  assessment: "correct" | "partial" | "incorrect";
  hintLevel: number;
  durationMs: number;
}): Promise<{ attemptId: string; mastery: MasteryState }> {
  return parse<{ attemptId: string; mastery: MasteryState }>(
    await fetch(`/api/learning/sessions/${sessionId}/attempts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  );
}
