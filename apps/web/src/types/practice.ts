import type { QuestionPayload } from "./question";
import type { ModelRun } from "./runtime";
import type { MistakeErrorReason, MistakeStatus } from "./mistake";
import type { ReviewTask } from "./review";
import type { EvaluationEvidence } from "./tutoring";

export type VariationStrategy =
  | "concept-foundation"
  | "condition-reading"
  | "parallel-calculation"
  | "step-completion"
  | "scaffolded-transfer"
  | "self-check";

export type VariationLevel = "foundation" | "parallel" | "transfer";

/** 一道持久化验证题，以及可在答错后修正的结构化判题结果。 */
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
  attemptId?: string;
  evaluationEvidence?: EvaluationEvidence;
  /** Returned by the answer endpoint when deterministic evidence changes the tutor stage. */
  tutorStage?: "practice" | "verify";
  mastery?: {
    correctStreak: number;
    requiredCorrect: number;
    mastered: boolean;
    answeredCount: number;
  };
}

export interface VariationAttempt {
  attemptId: string;
  variationId: string;
  mistakeId: string;
  learnerId: string;
  attemptNumber: number;
  response: { content?: string; interactionResult?: Record<string, unknown> };
  evaluationEvidence: EvaluationEvidence;
  assessment: "correct" | "partial" | "incorrect";
  feedback: string;
  createdAt: number;
}

export interface MistakeEvidence {
  mistakeId: string;
  learnerId: string;
  errorReason?: MistakeErrorReason;
  status: MistakeStatus;
  masteryTransition: string;
  variations: Array<{
    variationId: string;
    sequence: number;
    strategy: string;
    strategyVersion?: string;
    target?: string;
    objective?: string;
    level: VariationLevel;
    attempts: VariationAttempt[];
  }>;
  reviewTasks: ReviewTask[];
}
