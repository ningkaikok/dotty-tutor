import type { QuestionPayload } from "./question";
import type { RevisionSummary, RunSummary } from "./textbook";

export type PublicationStatus = "draft" | "in_review" | "published" | "archived";

export interface PublicationSummary {
  publicationId: string;
  title: string;
  sourceUploadId?: string;
  status: PublicationStatus;
  version: number;
  revisionOf?: string | null;
  lessonIds: string[];
  lessonCount: number;
  createdAt: number;
  updatedAt: number;
  qualityRecovery?: {
    status: "recovered";
    publishedCount: number;
    quarantinedCount: number;
    quarantinedLessonIds: string[];
  };
}

export interface PublicationRevisionResult {
  publication: PublicationSummary;
  questionPayloads: QuestionPayload[];
  run: RunSummary;
  revisions?: RevisionSummary[];
}

export interface PublicationWorkspaceState {
  publication: PublicationSummary | null;
  questionPayloads: QuestionPayload[];
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
