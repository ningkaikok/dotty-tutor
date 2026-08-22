import type { QuestionPayload } from "../types/question";
import type {
  BatchProcessResult,
  BackgroundJob,
  FullPaperResult,
  FullPaperSummary,
  LibraryItem,
  PdfUploadTask,
  QuestionRegenerationResult,
  TextbookImportResult,
} from "../types/textbook";
import { GeneratedSuccess, parse } from "./client";

type GeneratedQuestionRepairResponse = GeneratedSuccess<"regenerate_question_api_uploads__upload_id__questions__question_source_key__regenerate_post">;

export async function loadQuestion(): Promise<QuestionPayload> {
  return parse<QuestionPayload>(await fetch("/api/question"));
}

export async function importTextbook(file: File, sourceText = ""): Promise<TextbookImportResult> {
  const body = new FormData();
  body.append("file", file);
  body.append("sourceText", sourceText);
  return parse<TextbookImportResult>(await fetch("/api/textbook/import", { method: "POST", body }));
}

export async function initPdfUpload(file: File, chunkSize: number, sourceText = ""): Promise<PdfUploadTask> {
  return parse<PdfUploadTask>(await fetch("/api/uploads/init", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      filename: file.name,
      size: file.size,
      contentType: file.type || "application/pdf",
      chunkSize,
      totalChunks: Math.ceil(file.size / chunkSize),
      sourceText,
    }),
  }));
}

export async function uploadPdfChunk(uploadId: string, index: number, chunk: Blob) {
  return parse<{ index: number; received: number; uploadedCount: number }>(
    await fetch(`/api/uploads/${uploadId}/chunks/${index}`, {
      method: "PUT",
      headers: { "Content-Type": "application/octet-stream" },
      body: chunk,
    }),
  );
}

export async function loadPdfUploadStatus(uploadId: string): Promise<PdfUploadTask> {
  return parse<PdfUploadTask>(await fetch(`/api/uploads/${uploadId}/status`, { cache: "no-store" }));
}

export async function completePdfUpload(uploadId: string): Promise<BackgroundJob<TextbookImportResult>> {
  return parse<BackgroundJob<TextbookImportResult>>(await fetch(`/api/uploads/${uploadId}/complete`, { method: "POST" }));
}

export async function loadBackgroundJob<T = unknown>(jobId: string): Promise<BackgroundJob<T>> {
  return parse<BackgroundJob<T>>(await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, { cache: "no-store" }));
}

export async function cancelBackgroundJob<T = unknown>(jobId: string): Promise<BackgroundJob<T>> {
  return parse<BackgroundJob<T>>(await fetch(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" }));
}

export async function retryBackgroundJob<T = unknown>(jobId: string): Promise<BackgroundJob<T>> {
  return parse<BackgroundJob<T>>(await fetch(`/api/jobs/${encodeURIComponent(jobId)}/retry`, { method: "POST" }));
}

export async function processPdfBatch(
  uploadId: string,
  batchId: string,
  force = false,
  refreshOcr = false,
): Promise<BatchProcessResult> {
  const queued = await parse<BackgroundJob<BatchProcessResult>>(
    await fetch(`/api/uploads/${uploadId}/batches/${batchId}/process?force=${force}&refreshOcr=${refreshOcr}`, { method: "POST" }),
  );
  let job = queued;
  while (job.status === "queued" || job.status === "running") {
    await new Promise((resolve) => window.setTimeout(resolve, 800));
    job = await loadBackgroundJob<BatchProcessResult>(queued.jobId);
  }
  if (job.status !== "succeeded" || !job.result) {
    throw new Error(job.lastError?.message || job.message || "批次处理失败");
  }
  return job.result;
}

export async function generateFullPaper(uploadId: string): Promise<BackgroundJob<FullPaperResult>> {
  return parse<BackgroundJob<FullPaperResult>>(
    await fetch(`/api/uploads/${encodeURIComponent(uploadId)}/full-paper`, { method: "POST" }),
  );
}

export async function loadFullPaperSummary(uploadId: string): Promise<{
  uploadId: string;
  job: BackgroundJob<FullPaperResult> | null;
  summary: FullPaperSummary;
  questionPayloads: QuestionPayload[];
}> {
  return parse(await fetch(`/api/uploads/${encodeURIComponent(uploadId)}/full-paper/summary`, { cache: "no-store" }));
}

export async function regenerateQuestion(
  uploadId: string,
  sourceQuestionKey: string,
  refreshOcr = false,
): Promise<QuestionRegenerationResult> {
  return parse<QuestionRegenerationResult & GeneratedQuestionRepairResponse>(
    await fetch(
      `/api/uploads/${encodeURIComponent(uploadId)}/questions/${encodeURIComponent(sourceQuestionKey)}/regenerate?refreshOcr=${refreshOcr}`,
      { method: "POST" },
    ),
  );
}

export async function loadLibrary(): Promise<LibraryItem[]> {
  const response = await parse<{ items: LibraryItem[] }>(await fetch("/api/library", { cache: "no-store" }));
  return response.items;
}

export async function loadLibraryItem(uploadId: string): Promise<TextbookImportResult> {
  return parse<TextbookImportResult>(await fetch(`/api/library/${uploadId}`, { cache: "no-store" }));
}

export async function deleteLibraryItem(uploadId: string): Promise<void> {
  await parse<{ status: string }>(await fetch(`/api/library/${uploadId}`, { method: "DELETE" }));
}
