import type { ExerciseAttemptInput, LearningSession, MasteryState } from "../types/lesson";
import { parse } from "./client";

export async function createLearningSession(input: { learnerId: string; publicationId: string }): Promise<LearningSession> {
  return parse<LearningSession>(await fetch("/api/learning/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function loadLearningMastery(learnerId: string): Promise<MasteryState[]> {
  const payload = await parse<{ learnerId: string; items: MasteryState[] }>(
    await fetch(`/api/learning/mastery/${encodeURIComponent(learnerId)}`, { cache: "no-store" }),
  );
  return payload.items;
}

export async function loadLearningSession(sessionId: string): Promise<LearningSession & { attempts: unknown[] }> {
  return parse<LearningSession & { attempts: unknown[] }>(
    await fetch(`/api/learning/sessions/${sessionId}`, { cache: "no-store" }),
  );
}

export async function recordExerciseAttempt(
  sessionId: string,
  input: ExerciseAttemptInput,
): Promise<{ attemptId: string; mastery: MasteryState }> {
  return parse<{ attemptId: string; mastery: MasteryState }>(
    await fetch(`/api/learning/sessions/${sessionId}/attempts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  );
}

export async function syncExerciseAttempts(
  sessionId: string,
  attempts: ExerciseAttemptInput[],
): Promise<{ sessionId: string; synced: Array<{ attemptId: string; mastery: MasteryState }> }> {
  return parse(await fetch(`/api/learning/sessions/${sessionId}/sync`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ attempts }),
  }));
}
