import type { Question, QuestionPayload } from "./question";
import type { EvaluationEvidence } from "./tutoring";

export interface StructuredAnswerInput {
  content: string;
  interactionResult: Record<string, unknown>;
}

export interface ReviewTask {
  taskId: string;
  mistakeId: string;
  learnerId: string;
  intervalDays: number;
  dueAt: number;
  status: "scheduled" | "ready" | "completed" | "cancelled" | "superseded";
  questionPayload?: QuestionPayload;
  response: StructuredAnswerInput | Record<string, never>;
  assessment?: "correct" | "partial" | "incorrect";
  /** Older review responses may omit evidence until the persisted response contains it. */
  evaluationEvidence?: EvaluationEvidence;
  feedback: string;
  createdAt: number;
  startedAt?: number;
  completedAt?: number;
  scheduleVersion?: string;
  sequenceNo?: number;
  profile?: string;
  objectiveType?: "memory" | "procedural" | "conceptual" | "design" | "unknown";
  gateMode?: "quantitative" | "qualitative" | "legacy";
  policyVersion?: string;
  triggerEvidenceRef?: string | null;
  supersededAt?: number | null;
  policy?: {
    objectiveType: string;
    gateMode: string;
    policyVersion: string;
    profile: string;
  };
  gate?: {
    mode: string;
    passed?: boolean;
    nextAction?: "stay" | "advance" | "test_out" | "needs_review";
    [key: string]: unknown;
  };
  nextAction?: "stay" | "advance" | "test_out" | "needs_review";
  mistake?: {
    chapter: string;
    knowledgePoint: string;
    prompt: string;
  };
}

export interface KnowledgeProgress {
  knowledgePoint: string;
  total: number;
  mastered: number;
}

export interface LearningProgress {
  learnerId: string;
  totalMistakes: number;
  masteredCount: number;
  masteryRate: number;
  dueReviewCount: number;
  completedReviewCount: number;
  reviewAccuracy: number;
  verificationAccuracy: number | null;
  reviewCompletionRate: number | null;
  sameKnowledgePointReerrorRate: number | null;
  knowledgePoints: KnowledgeProgress[];
}

export interface AnswerDraft {
  question: Question;
  selectedOptions: string[];
  blankAnswers: Record<string, string>;
  numericAnswer: string;
}
