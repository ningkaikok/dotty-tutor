import type { TutorCanvasState, TutorFormulaRecognition, TutorInput, TutorReply, TutorThread, TutorTurnResult } from "../types/tutoring";
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
    inputId?: string;
    formulaRecognitions?: TutorFormulaRecognition[];
    canvasState?: TutorCanvasState;
  },
): Promise<TutorTurnResult> {
  return parse<TutorTurnResult>(await fetch(`/api/tutor/threads/${threadId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }));
}

export async function createTutorInput(
  threadId: string,
  input: {
    content: string;
    mode: "text" | "structured";
    interactionResult: Record<string, unknown>;
    formulaRecognitions?: TutorFormulaRecognition[];
    canvasState?: TutorCanvasState;
    photo?: File;
    questionImage?: File;
  },
): Promise<TutorInput> {
  const form = new FormData();
  form.set("content", input.content);
  form.set("mode", input.mode);
  form.set("interactionResult", JSON.stringify(input.interactionResult));
  form.set("formulaRecognitions", JSON.stringify(input.formulaRecognitions ?? []));
  if (input.canvasState) form.set("canvasState", JSON.stringify(input.canvasState));
  if (input.photo) form.set("photo", input.photo);
  if (input.questionImage) form.set("questionImage", input.questionImage);
  return parse<TutorInput>(await fetch(`/api/tutor/threads/${threadId}/inputs`, { method: "POST", body: form }));
}

export async function decideTutorObservation(
  inputId: string,
  decision: { decision: "confirm" | "correct" | "reject"; correctedText?: string },
): Promise<TutorInput> {
  return parse<TutorInput>(await fetch(`/api/tutor/inputs/${inputId}/observations`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(decision),
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
