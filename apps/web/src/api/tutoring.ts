import type { TutorReply, TutorThread, TutorTurnResult } from "../types/tutoring";
import { parse } from "./client";
import { currentLearnerId } from "./identity";

export async function createTutorThread(
  mistakeId: string,
  learnerId: string = currentLearnerId(),
): Promise<TutorThread> {
  return parse<TutorThread>(
    await fetch(`/api/mistakes/${mistakeId}/thread?learnerId=${encodeURIComponent(learnerId)}`, {
      method: "POST",
    }),
  );
}

export async function loadTutorThread(threadId: string): Promise<TutorThread> {
  return parse<TutorThread>(await fetch(`/api/tutor/threads/${threadId}`, { cache: "no-store" }));
}

export async function sendTutorMessage(
  threadId: string,
  input: {
    content: string;
    mode: "answer" | "help";
    hintLevel: number;
    interactionResult?: Record<string, unknown>;
  },
): Promise<TutorTurnResult> {
  return parse<TutorTurnResult>(await fetch(`/api/tutor/threads/${threadId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function requestHelp(input: {
  questionId: string;
  publicationId?: string;
  studentInput: string;
  hintLevel: number;
  mode: "answer" | "help";
  interactionResult?: Record<string, unknown>;
}): Promise<TutorReply> {
  return parse<TutorReply>(await fetch("/api/help", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...input, language: "zh" }),
  }));
}
