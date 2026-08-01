import type { BatchProcessResult, LibraryItem, ModelCatalog, ModelProvider, OcrCatalog, OcrProvider, PdfUploadTask, QuestionPayload, TextbookImportResult, TutorReply } from "./types";

async function parse<T>(response: Response): Promise<T> {
  const data = await response.json().catch(() => null) as (T & { detail?: string }) | null;
  if (!response.ok) {
    throw new Error(data?.detail || `请求失败：${response.status}`);
  }
  if (!data) throw new Error("后端返回了无法解析的数据");
  return data;
}

export async function loadQuestion(): Promise<QuestionPayload> {
  return parse<QuestionPayload>(await fetch("/api/question"));
}

export async function loadModels(): Promise<ModelCatalog> {
  return parse<ModelCatalog>(await fetch("/api/models"));
}

export async function selectModel(provider: ModelProvider, model: string): Promise<ModelCatalog> {
  return parse<ModelCatalog>(
    await fetch("/api/models/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, model }),
    }),
  );
}

export async function loadOcrProviders(): Promise<OcrCatalog> {
  return parse<OcrCatalog>(await fetch("/api/ocr"));
}

export async function selectOcrProvider(provider: OcrProvider): Promise<OcrCatalog> {
  return parse<OcrCatalog>(
    await fetch("/api/ocr/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider }),
    }),
  );
}

export async function importTextbook(file: File, sourceText = ""): Promise<TextbookImportResult> {
  const body = new FormData();
  body.append("file", file);
  body.append("sourceText", sourceText);
  return parse<TextbookImportResult>(
    await fetch("/api/textbook/import", { method: "POST", body }),
  );
}

export async function initPdfUpload(file: File, chunkSize: number, sourceText = ""): Promise<PdfUploadTask> {
  return parse<PdfUploadTask>(
    await fetch("/api/uploads/init", {
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
    }),
  );
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
  return parse<PdfUploadTask>(
    await fetch(`/api/uploads/${uploadId}/status`, { cache: "no-store" }),
  );
}

export async function completePdfUpload(uploadId: string): Promise<TextbookImportResult> {
  return parse<TextbookImportResult>(
    await fetch(`/api/uploads/${uploadId}/complete`, { method: "POST" }),
  );
}

export async function requestHelp(input: {
  questionId: string;
  studentInput: string;
  hintLevel: number;
  mode: "answer" | "help";
  interactionResult?: { connections: string[][] };
}): Promise<TutorReply> {
  return parse<TutorReply>(
    await fetch("/api/help", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...input, language: "zh" }),
    }),
  );
}

export async function processPdfBatch(uploadId: string, batchId: string, force = false): Promise<BatchProcessResult> {
  return parse<BatchProcessResult>(
    await fetch(`/api/uploads/${uploadId}/batches/${batchId}/process?force=${force}`, { method: "POST" }),
  );
}

export async function loadLibrary(): Promise<LibraryItem[]> {
  const response = await parse<{ items: LibraryItem[] }>(await fetch("/api/library", { cache: "no-store" }));
  return response.items;
}

export async function loadLibraryItem(uploadId: string): Promise<TextbookImportResult> {
  return parse<TextbookImportResult>(await fetch(`/api/library/${uploadId}`, { cache: "no-store" }));
}
