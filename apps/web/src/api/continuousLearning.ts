import { parse } from "./client";

export type ContinuousLearningKind =
  | "assignment"
  | "confirm_mistake"
  | "correct_mistake"
  | "review"
  | "practice"
  | "none";

export interface ContinuousLearningAction {
  kind: ContinuousLearningKind;
  title?: string;
  assignmentId?: string;
  publicationId?: string;
  learnerStatus?: string;
  attemptedCount?: number;
  questionCount?: number;
  sessionId?: string | null;
  mistakeId?: string;
  taskId?: string;
  status?: string;
  dueAt?: number;
  legacyPublishedPending?: boolean;
  [key: string]: unknown;
}

export interface ContinuousLearningSnapshot {
  learnerId: string;
  serverTime: number;
  next: ContinuousLearningAction;
  summary: {
    unfinishedAssignments: number;
    pendingConfirmationCount: number;
    unmasteredCount: number;
    dueReviewCount: number;
  };
}

export async function loadNextLearningAction(learnerId: string): Promise<ContinuousLearningSnapshot> {
  return parse<ContinuousLearningSnapshot>(await fetch(
    `/api/learning/next?learnerId=${encodeURIComponent(learnerId)}`,
    { cache: "no-store" },
  ));
}

/** A POST is the student's explicit start signal; it may prepare one due review question. */
export async function startNextLearningAction(learnerId: string): Promise<ContinuousLearningSnapshot> {
  return parse<ContinuousLearningSnapshot>(await fetch("/api/learning/next/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ learnerId }),
  }));
}
