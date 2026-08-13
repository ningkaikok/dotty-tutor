import type { QuestionPayload } from "./question";
import type { ModelRun, OcrRun, ReviewRun } from "./runtime";

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
  };
  batches?: Array<{
    id: string;
    startPage: number;
    endPage: number;
    pageCount: number;
    status: "processed" | "queued" | "failed";
    error?: string;
  }>;
  questionPayload: QuestionPayload;
  questionPayloads?: QuestionPayload[];
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
    refreshOcr: boolean;
  };
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
}
