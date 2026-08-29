import type { QuestionPayload } from "./question";
import type { ModelRun, OcrRun, ReviewRun } from "./runtime";

export interface RunSummary {
  runId: string;
  operation: "question_repair" | "question_reocr" | "batch_regenerate" | "publication_rereview" | "initial_batch";
  scope: string;
  targetUploadId?: string | null;
  targetQuestionKey?: string | null;
  targetPublicationId?: string | null;
  status: "running" | "succeeded" | "failed";
  config: Record<string, unknown>;
  result?: Record<string, unknown> | null;
  error?: Record<string, unknown> | null;
  startedAt: number;
  completedAt?: number | null;
}

export interface RevisionSummary {
  revisionId: string;
  uploadId: string;
  sourceQuestionKey: string;
  revisionNumber: number;
  operation: RunSummary["operation"];
  previousRevisionId?: string | null;
  runId: string;
  createdAt: number;
}

export interface ImportStage {
  id: string;
  label: string;
  status: "done";
}

export interface TextbookImportResult {
  uploadId?: string;
  importId: string;
  filename: string;
  contentType: string;
  size: number;
  stored: boolean;
  modelRun: ModelRun;
  ocrRun: OcrRun;
  reviewRun?: ReviewRun;
  stages: ImportStage[];
  extraction: {
    chapter: string;
    knowledgePoint: string;
    questionCount: number;
    questionLimit?: number;
    formulaCount: number;
    guideCardCount: number;
    pageCount?: number;
    batchCount?: number;
    confidence: number;
    mode: string;
    processablePageCount?: number;
    pageLimit?: number;
    truncated?: boolean;
  };
  batches?: Array<{
    id: string;
    startPage: number;
    endPage: number;
    pageCount: number;
    status: "processed" | "queued" | "failed";
    error?: string;
  }>;
  qualityReport?: ImportQualityReport;
  questionPayload: QuestionPayload;
  questionPayloads?: QuestionPayload[];
  fullPaper?: FullPaperSummary;
}

export interface FullPaperBatchSummary {
  id: string;
  status: "succeeded" | "failed" | "skipped";
  questionCount: number;
  quarantinedQuestions?: number;
  error?: string;
}

export interface FullPaperSummary {
  totalBatches: number;
  processedBatches: number;
  succeededBatches: number;
  failedBatches: number;
  quarantinedQuestions: number;
  skippedBatches: number;
  questionCount: number;
  questionLimit?: number;
  limitReached?: boolean;
  batches: FullPaperBatchSummary[];
  qualityReport?: ImportQualityReport;
  blockedByQualityReport?: boolean;
}

export interface ImportQualityReport {
  scope?: "preview" | "full-paper";
  status: "ready" | "warning" | "blocked";
  readyForFullPaper: boolean;
  totalPages: number;
  expectedQuestionCount: number;
  detectedQuestionNumbers: string[];
  questionRange: string;
  duplicateQuestionNumbers: string[];
  missingQuestionNumbers: number[];
  unidentifiedPages: number[];
  imageAttributionConflicts: Array<{ image: string; questionNumbers: string[] }>;
  warnings: string[];
  blockers: string[];
  checkedBatchCount: number;
}

export interface FullPaperResult {
  summary: FullPaperSummary;
  questionPayload?: QuestionPayload | null;
  questionPayloads: QuestionPayload[];
  batches?: TextbookImportResult["batches"];
}

export interface LibraryItem {
  uploadId: string;
  importId: string;
  filename: string;
  size: number;
  status: "complete";
  questionCount: number;
  pageCount?: number;
  chapter: string;
  updatedAt: number;
}

export interface BatchProcessResult {
  batch: NonNullable<TextbookImportResult["batches"]>[number];
  questionPayload: QuestionPayload;
  questionPayloads?: QuestionPayload[];
  ocrRun: OcrRun;
  modelRun: ModelRun;
  modelRuns?: ModelRun[];
  reviewRun?: ReviewRun;
  reviewRuns?: ReviewRun[];
  run?: RunSummary | null;
  revisions?: RevisionSummary[];
}

export interface QuestionRegenerationResult {
  batch: NonNullable<TextbookImportResult["batches"]>[number];
  questionPayload: QuestionPayload;
  guideCards: Array<Record<string, unknown>>;
  ocrRun: OcrRun;
  modelRun: ModelRun;
  reviewRun?: ReviewRun;
  regeneration: {
    scope: "question";
    operation?: "question_repair" | "question_reocr";
    refreshOcr: boolean;
  };
  run: RunSummary;
  revision?: RevisionSummary | null;
}

export interface PdfUploadTask {
  uploadId: string;
  filename: string;
  size: number;
  chunkSize: number;
  totalChunks: number;
  uploadedChunks: number[];
  status: "uploading" | "merging" | "validating" | "splitting" | "ocr" | "generating" | "complete" | "failed";
  progress: number;
  message: string;
  elapsedSeconds: number;
  result?: TextbookImportResult;
  jobId?: string;
  jobStatus?: BackgroundJobStatus;
  attemptCount?: number;
}

export type BackgroundJobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface BackgroundJob<T = unknown> {
  jobId: string;
  jobType: string;
  status: BackgroundJobStatus;
  progress: number;
  message: string;
  attemptCount: number;
  maxAttempts: number;
  cancelRequested: boolean;
  lastError?: { message?: string; code?: string; [key: string]: unknown } | null;
  result?: T | null;
  createdAt: number;
  updatedAt: number;
  startedAt?: number | null;
  completedAt?: number | null;
}
