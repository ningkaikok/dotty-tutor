import { parse } from "./client";

const MAX_WHOLE_BOOK_PAGES = 1000;
const MAX_WHOLE_BOOK_BATCH_PAGES = 30;

export const WHOLE_BOOK_LIMITS = {
  maxPages: MAX_WHOLE_BOOK_PAGES,
  pagesPerBatch: MAX_WHOLE_BOOK_BATCH_PAGES,
  maxBatches: Math.ceil(MAX_WHOLE_BOOK_PAGES / MAX_WHOLE_BOOK_BATCH_PAGES),
  maxGeneratedPagesPerBatch: 45,
} as const;

export type WholeBookStatus =
  | "queued" | "running" | "waiting_for_source" | "paused" | "needs_attention"
  | "source_changed" | "completed";

export type WholeBookBatchStatus =
  | "queued" | "source_preparation" | "planning" | "generating" | "checking"
  | "repairing" | "published" | "completed" | "needs_attention" | "failed" | "source_changed";

export interface WholeBookStartInput {
  mode: "whole_book";
  confirmedMaterialKind?: "textbook";
  subject: string;
  title?: string;
  sourceTitle?: string;
  sourceUrl?: string;
  license?: string;
  attributionText?: string;
  distributionScope: "private" | "classroom" | "public";
}

export interface WholeBookBatchView {
  batchIndex: number;
  title: string;
  pageStart: number;
  pageEnd: number;
  status: WholeBookBatchStatus;
  message: string;
  chapterId?: string | null;
  coursewareIds: string[];
  publicationIds: string[];
  skippedPages: Array<{ page: number; reason?: string; note?: string }>;
  error?: { code: string; message: string; retryable?: boolean; stage?: string } | null;
}

export interface WholeBookRunView {
  runId: string;
  uploadId: string;
  filename: string;
  status: WholeBookStatus;
  message: string;
  totalPages: number;
  completedPages: number;
  completedBatches: number;
  batchCount: number;
  currentBatchIndex?: number | null;
  batches: WholeBookBatchView[];
  canPause: boolean;
  canResume: boolean;
  createdAt: number;
  updatedAt: number;
}

export async function startWholeBook(uploadId: string, input: WholeBookStartInput): Promise<WholeBookRunView> {
  return parse<WholeBookRunView>(await fetch(
    `/api/uploads/${encodeURIComponent(uploadId)}/courseware/whole-book`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) },
  ));
}

export async function loadWholeBookForUpload(uploadId: string): Promise<WholeBookRunView> {
  return parse<WholeBookRunView>(await fetch(
    `/api/uploads/${encodeURIComponent(uploadId)}/courseware/whole-book`, { cache: "no-store" },
  ));
}

export async function loadWholeBookRun(runId: string): Promise<WholeBookRunView> {
  return parse<WholeBookRunView>(await fetch(
    `/api/courseware/whole-book-runs/${encodeURIComponent(runId)}`, { cache: "no-store" },
  ));
}

export async function pauseWholeBook(runId: string, commandId: string): Promise<WholeBookRunView> {
  return wholeBookControl(runId, "pause", commandId);
}

export async function resumeWholeBook(runId: string, commandId: string): Promise<WholeBookRunView> {
  return wholeBookControl(runId, "resume", commandId);
}

async function wholeBookControl(
  runId: string,
  action: "pause" | "resume",
  commandId: string,
): Promise<WholeBookRunView> {
  return parse<WholeBookRunView>(await fetch(
    `/api/courseware/whole-book-runs/${encodeURIComponent(runId)}/${action}`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ commandId }) },
  ));
}
