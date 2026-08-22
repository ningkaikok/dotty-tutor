import type { QuestionPayload } from "./question";
import type { ModelRun, OcrRun } from "./runtime";

export type MistakeErrorReason =
  | "concept"
  | "reading"
  | "calculation"
  | "missing_step"
  | "unknown"
  | "careless";

export type MistakeStatus = "pending_confirmation" | "unmastered" | "mastered" | "archived";

export interface MistakeItem {
  mistakeId: string;
  learnerId: string;
  sourceFilename: string;
  contentType: string;
  sourceImageUrl: string;
  questionPayload: QuestionPayload;
  guideCards: Array<Record<string, unknown>>;
  ocrRun: OcrRun;
  modelRun: ModelRun;
  originalAnswer: string;
  subject: string;
  gradeBand: string;
  chapter: string;
  knowledgePoint: string;
  errorReason?: MistakeErrorReason;
  notes: string;
  status: MistakeStatus;
  createdAt: number;
  updatedAt: number;
  confirmedAt?: number;
}

export interface MistakeConfirmation {
  prompt: string;
  originalAnswer: string;
  subject: string;
  gradeBand: string;
  chapter: string;
  knowledgePoint: string;
  errorReason: MistakeErrorReason;
  notes: string;
}
