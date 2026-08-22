import type { LearningProgress, ReviewTask, StructuredAnswerInput } from "../types/review";
import { parse } from "./client";
import { DEMO_LEARNER_ID } from "./client";

export async function loadReviews(): Promise<{ items: ReviewTask[]; serverTime: number }> {
  return parse(await fetch(`/api/reviews?learnerId=${DEMO_LEARNER_ID}`, { cache: "no-store" }));
}

export async function loadLearningProgress(): Promise<LearningProgress> {
  return parse(await fetch(`/api/progress?learnerId=${DEMO_LEARNER_ID}`, { cache: "no-store" }));
}

export async function startReview(taskId: string): Promise<ReviewTask> {
  return parse(await fetch(`/api/reviews/${taskId}/start`, { method: "POST" }));
}

export async function answerReview(taskId: string, input: StructuredAnswerInput): Promise<ReviewTask> {
  return parse(await fetch(`/api/reviews/${taskId}/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }));
}
