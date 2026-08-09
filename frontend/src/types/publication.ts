import type { QuestionPayload } from "./question";

export type PublicationStatus = "draft" | "in_review" | "published" | "archived";

export interface PublicationSummary {
  publicationId: string;
  title: string;
  sourceUploadId?: string;
  status: PublicationStatus;
  lessonIds: string[];
  lessonCount: number;
  createdAt: number;
  updatedAt: number;
}

export interface PublishedLesson {
  lessonId: string;
  title: string;
  version: number;
  status: string;
  questionPayload: QuestionPayload;
  guideCards: Array<Record<string, unknown>>;
}

export interface PublicationDetail extends PublicationSummary {
  lessons: PublishedLesson[];
}
