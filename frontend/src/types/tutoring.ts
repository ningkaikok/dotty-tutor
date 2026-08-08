import type { CanvasAction } from "./question";
import type { ModelRun } from "./runtime";

/** The tutor can advance only through these explainable phase-three states. */
export type TutorStage = "diagnose" | "explain" | "practice" | "verify";

export interface GuideContext {
  assessment?: "correct" | "partial" | "incorrect";
  stuckAt?: string;
  knowledge?: string[];
  hint?: string;
  question?: string;
}

export interface TutorReply {
  reply: string;
  guideContext: GuideContext;
  nextHintLevel: number;
  canvasAction: CanvasAction;
  source: "stored-guide-card" | "answer-check" | "model-generated";
  modelRun: ModelRun;
}

export interface TutorMessage {
  messageId: string;
  threadId: string;
  role: "student" | "assistant";
  content: string;
  inputMode: "text" | "structured";
  assessment?: "correct" | "partial" | "incorrect";
  action: Record<string, unknown>;
  modelRun: ModelRun | Record<string, never>;
  createdAt: number;
}

export interface TutorThread {
  threadId: string;
  mistakeId: string;
  learnerId: string;
  stage: TutorStage;
  summary: string;
  hintLevel: number;
  messageCount: number;
  messages?: TutorMessage[];
  createdAt: number;
  updatedAt: number;
}

export interface TutorTurnResult {
  thread: TutorThread;
  reply: TutorReply;
  action: {
    type: "advance_stage" | "continue_stage";
    previousStage: TutorStage;
    nextStage: TutorStage;
    assessment: "correct" | "partial" | "incorrect";
    prompt: string;
  };
}
