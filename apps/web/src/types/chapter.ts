import type { components } from "./generated/api";
import type { LessonBlock, LessonDocument } from "./lesson";
import type { QuestionType } from "./question";

export type ChapterSubject = components["schemas"]["ChapterCreate"]["subject"];
export type ChapterStatus = "draft" | "needs_review" | "in_review" | "published";
export type ChapterRegion = components["schemas"]["ChapterRegion"];
export type ChapterSourcePage = components["schemas"]["ChapterPage"];
export type ChapterSourceFlag = NonNullable<components["schemas"]["ChapterPageFlag"]["flags"]>[number];
export type ChapterSource = components["schemas"]["ChapterSource"];
export type ChapterSourceRevision = components["schemas"]["ChapterSourceRevisionView"];
export type ChapterReviewIssue = components["schemas"]["ChapterIssue"];
export type ChapterEvidenceRef = components["schemas"]["ChapterEvidenceRef"];
export type ChapterEvidenceOption = components["schemas"]["ChapterEvidenceOption"];
export type ChapterSourceLocator = Omit<components["schemas"]["ChapterSourceLocator"], "regions"> & { regions: ChapterRegion[] };

export interface ChapterPublicQuestion {
  id: string;
  prompt: string;
  questionType: QuestionType;
  chapter?: string;
  knowledgePoint?: string;
  options?: string[];
  givens?: string[];
}

export interface ChapterAuthorQuestion extends ChapterPublicQuestion {
  correctAnswers?: string[];
  answerSpec?: { expected?: string; accepted?: string[]; answerType?: string };
  questionKind?: "word_meaning" | "reference" | "explicit" | "inference" | "short_answer";
  answerMode?: "objective" | "short_answer";
  acceptedAnswers?: string[];
  requiredEvidenceRefs?: ChapterEvidenceRef[];
  rubric?: Record<string, unknown>;
}

export interface ChapterLesson extends Omit<LessonDocument, "blocks" | "questionPayload" | "status"> {
  status: "draft" | "in_review" | "approved" | "needs_review" | "published";
  blocks: LessonBlock[];
  questionPayload?: { question: ChapterAuthorQuestion };
  sourceRevisionId: string;
  sourceLocator: ChapterSourceLocator;
  evidenceOptions?: ChapterEvidenceOption[];
  reviewIssues: ChapterReviewIssue[];
}

export interface ChapterManagement {
  chapterId: string;
  subject: ChapterSubject;
  title: string;
  status: ChapterStatus;
  version: number;
  recordVersion: number;
  sourceRevisions: ChapterSourceRevision[];
  lessons: ChapterLesson[];
  currentLessonIds: string[];
  reviewIssues: ChapterReviewIssue[];
  publicationId: string | null;
  publications: components["schemas"]["ChapterPublicationView"][];
}

export type ChapterSummary = components["schemas"]["ChapterSummaryView"];

export interface ChapterLessonEditInput extends Omit<components["schemas"]["ChapterLessonEdit"], "expectedRecordVersion"> {
  requiredEvidenceRefs: ChapterEvidenceRef[];
  rubric: Record<string, unknown>;
}

export interface PublishedChapter {
  chapterId: string;
  subject: ChapterSubject;
  title: string;
  publicationId: string;
  version: number;
  status: "published";
  lessons: Array<Omit<ChapterLesson, "questionPayload"> & { questionPayload?: { question: ChapterPublicQuestion } }>;
}

export interface ChapterAttemptInput {
  attemptId: string;
  lessonId: string;
  questionId: string;
  learnerId?: string;
  publicationId?: string;
  answer: { text?: string; numericAnswer?: string; selectedOptions?: string[] };
  evidenceRefs: ChapterEvidenceRef[];
}

export interface ChapterAttemptResult {
  attemptId: string;
  publicationId: string;
  lessonId: string;
  learnerId?: string | null;
  answer: Record<string, unknown>;
  evidenceRefs: ChapterEvidenceRef[];
  assessment: "correct" | "incorrect" | "needs_review";
  evidenceVerdict: "supported" | "mismatch" | "missing" | "needs_review";
  feedback: { message: string; evidence?: Record<string, unknown> };
  mastery?: Record<string, unknown> | null;
  reviews?: Array<{ reviewer: string; decision: "correct" | "incorrect"; note: string; createdAt?: number }>;
}
export type ChapterAttemptReviewInput = components["schemas"]["ChapterAttemptReview"];
