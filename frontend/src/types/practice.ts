import type { QuestionPayload } from "./question";
import type { ModelRun } from "./runtime";

export type VariationStrategy =
  | "concept-foundation"
  | "condition-reading"
  | "parallel-calculation"
  | "step-completion"
  | "scaffolded-transfer"
  | "self-check";

export type VariationLevel = "foundation" | "parallel" | "transfer";

/** One immutable generated exercise and, after submission, its final result. */
export interface VariationExercise {
  variationId: string;
  mistakeId: string;
  learnerId: string;
  strategy: VariationStrategy;
  level: VariationLevel;
  sequence: number;
  questionPayload: QuestionPayload;
  modelRun: ModelRun;
  status: "ready" | "answered";
  assessment?: "correct" | "partial" | "incorrect";
  response: {
    content?: string;
    interactionResult?: Record<string, unknown>;
  };
  feedback: string;
  createdAt: number;
  answeredAt?: number;
  mastery?: {
    correctStreak: number;
    requiredCorrect: number;
    mastered: boolean;
    answeredCount: number;
  };
}
