import type { Question, QuestionPayload } from "./question";
import type { ModelRun } from "./runtime";

export interface StructuredAnswerInput {
  content: string;
  interactionResult: Record<string, unknown>;
}

export interface ReviewTask {
  taskId: string;
  mistakeId: string;
  learnerId: string;
  intervalDays: 1 | 3 | 7;
  dueAt: number;
  status: "scheduled" | "ready" | "completed" | "cancelled";
  questionPayload?: QuestionPayload;
  modelRun: ModelRun | Record<string, never>;
  response: StructuredAnswerInput | Record<string, never>;
  assessment?: "correct" | "partial" | "incorrect";
  feedback: string;
  createdAt: number;
  startedAt?: number;
  completedAt?: number;
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
  knowledgePoints: KnowledgeProgress[];
}

export interface AnswerDraft {
  question: Question;
  selectedOptions: string[];
  blankAnswers: Record<string, string>;
  numericAnswer: string;
}
