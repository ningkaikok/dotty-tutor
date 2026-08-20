import { useEffect, useRef, useState } from "react";
import {
  completePdfUpload,
  cancelBackgroundJob,
  deleteLibraryItem,
  importTextbook,
  initPdfUpload,
  loadLibrary,
  loadLibraryItem,
  loadPdfUploadStatus,
  loadBackgroundJob,
  retryBackgroundJob,
  uploadPdfChunk,
} from "../../../api/textbooks";
import {
  loadModels,
  loadTutorModels,
  loadOcrProviders,
  loadReviewModels,
  selectModel,
  selectTutorModel,
  selectOcrProvider,
  selectReviewModel,
} from "../../../api/runtime";
import type {
  LibraryItem,
  ModelCatalog,
  ModelProvider,
  OcrCatalog,
  OcrProvider,
  PdfUploadTask,
  ReviewModelCatalog,
  TextbookImportResult,
  BackgroundJob,
} from "../../../types/index";
import {
  IMAGE_MAX_SIZE,
  PDF_CHUNK_SIZE,
  PDF_MAX_SIZE,
  isPdf,
  validatePdfEnvelope,
} from "./fileValidation";

export type UploadPhase = "idle" | "queued" | "uploading" | "paused" | "processing" | "error" | "done";

/** 每个运行时选择器独立维护请求状态，避免 OCR 请求锁住模型选择器。 */
export interface RuntimeLoadingState {
  generation: boolean;
  tutor: boolean;
  review: boolean;
  ocr: boolean;
}

export interface TextbookUploadItem {
  id: string;
  file: File;
  preview: string;
  phase: UploadPhase;
  progress: number;
  error: string;
  result: TextbookImportResult | null;
  processingTask: PdfUploadTask | null;
  pdfMode: boolean;
}

interface UploadController {
  task?: PdfUploadTask;
  uploaded: Set<number>;
  pauseRequested: boolean;
}

interface UseTextbookImportOptions {
  onOpenLibraryItem: (result: TextbookImportResult) => void;
}

const MAX_CONCURRENT_UPLOADS = 3;
const TRANSIENT_REQUEST_RETRIES = 4;

function isTransientRequestError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return error instanceof TypeError || /Failed to fetch|请求失败：[5]\d\d|网络|timeout/i.test(message);
}

async function withTransientRetry<T>(operation: () => Promise<T>): Promise<T> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await operation();
    } catch (error) {
      if (!isTransientRequestError(error) || attempt >= TRANSIENT_REQUEST_RETRIES) throw error;
      await new Promise((resolve) => window.setTimeout(resolve, 500 * (attempt + 1)));
    }
  }
}

function uploadIdFor(file: File): string {
  // 同一个文件允许再次上传，因此时间戳只用于 UI 键，不参与后端幂等标识。
  return `${file.name}-${file.size}-${file.lastModified}-${Math.random().toString(36).slice(2)}`;
}

/**
 * 教材导入状态机和全部 API 副作用的唯一拥有者。
 *
 * 每个 PDF 都有独立的控制器、轮询器和结果。界面只消费 uploads 列表，因此新增第二个
 * 文件不会覆盖第一个文件的进度；最多三个任务同时运行，避免本地 MinerU/模型进程被瞬间压垮。
 */
export function useTextbookImport({ onOpenLibraryItem }: UseTextbookImportOptions) {
  const [uploads, setUploads] = useState<TextbookUploadItem[]>([]);
  const uploadsRef = useRef<TextbookUploadItem[]>([]);
  const controllers = useRef(new Map<string, UploadController>());
  const running = useRef(new Set<string>());
  const [activeUploadId, setActiveUploadId] = useState("");
  const [sourceText, setSourceText] = useState("");
  const [models, setModels] = useState<ModelCatalog | null>(null);
  const [tutorModels, setTutorModels] = useState<ModelCatalog | null>(null);
  const [reviewModels, setReviewModels] = useState<ReviewModelCatalog | null>(null);
  const [ocrProviders, setOcrProviders] = useState<OcrCatalog | null>(null);
  const [runtimeLoading, setRuntimeLoading] = useState<RuntimeLoadingState>({
    generation: false,
    tutor: false,
    review: false,
    ocr: false,
  });
  const runtimeRequests = useRef(new Map<keyof RuntimeLoadingState, AbortController>());
  const [globalError, setGlobalError] = useState("");
  const [library, setLibrary] = useState<LibraryItem[]>([]);
  const [libraryLoadingId, setLibraryLoadingId] = useState("");
  const [deletingId, setDeletingId] = useState("");

  useEffect(() => {
    uploadsRef.current = uploads;
  }, [uploads]);

  useEffect(() => () => {
    // 页面离开时释放图片预览 URL；PDF 本身不复制到浏览器内存之外。
    uploadsRef.current.forEach((item) => {
      if (item.preview) URL.revokeObjectURL(item.preview);
    });
    runtimeRequests.current.forEach((controller) => controller.abort());
    runtimeRequests.current.clear();
  }, []);

  useEffect(() => {
    loadModels().then(setModels).catch(() => setGlobalError("模型列表加载失败"));
    loadTutorModels().then(setTutorModels).catch(() => setGlobalError("陪练模型列表加载失败"));
    loadOcrProviders().then(setOcrProviders).catch(() => setGlobalError("OCR 列表加载失败"));
    loadReviewModels().then(setReviewModels).catch(() => setGlobalError("审核模型列表加载失败"));
    loadLibrary().then(setLibrary).catch(() => setGlobalError("教材库加载失败"));
  }, []);

  const updateUpload = (id: string, patch: Partial<TextbookUploadItem>) => {
    setUploads((current) => current.map((item) => item.id === id ? { ...item, ...patch } : item));
  };

  const chooseFiles = (nextFiles?: FileList | File[]) => {
    if (!nextFiles) return;
    const files = Array.from(nextFiles);
    if (!files.length) return;
    const known = new Set(uploadsRef.current.map((item) => `${item.file.name}:${item.file.size}:${item.file.lastModified}`));
    const newItems = files
      .filter((file) => file.type === "application/pdf" || file.type.startsWith("image/") || file.name.toLowerCase().endsWith(".pdf"))
      .filter((file) => {
        const key = `${file.name}:${file.size}:${file.lastModified}`;
        if (known.has(key)) return false;
        known.add(key);
        return true;
      })
      .map((file): TextbookUploadItem => ({
        id: uploadIdFor(file),
        file,
        preview: file.type.startsWith("image/") ? URL.createObjectURL(file) : "",
        phase: "idle",
        progress: 0,
        error: "",
        result: null,
        processingTask: null,
        pdfMode: isPdf(file),
      }));
    if (!newItems.length) {
      setGlobalError("请选择 PDF 或图片文件；重复文件不会再次加入队列");
      return;
    }
    setGlobalError("");
    setUploads((current) => [...current, ...newItems]);
    setActiveUploadId((current) => current || newItems[0].id);
  };

  const runUpload = async (id: string) => {
    if (running.current.has(id)) return;
    const entry = uploadsRef.current.find((item) => item.id === id);
    if (!entry) return;
    running.current.add(id);
    const controller = controllers.current.get(id) ?? { uploaded: new Set<number>(), pauseRequested: false };
    controllers.current.set(id, controller);
    const text = sourceText;
    try {
      controller.pauseRequested = false;
      if (entry.pdfMode) {
        if (entry.file.size > PDF_MAX_SIZE) throw new Error("PDF 不能超过 500 MB");
        await validatePdfEnvelope(entry.file);
        let task = controller.task;
        if (!task) {
          task = await initPdfUpload(entry.file, PDF_CHUNK_SIZE, text);
          controller.task = task;
          controller.uploaded = new Set(task.uploadedChunks);
        }
        updateUpload(id, { phase: "uploading", error: "" });
        for (let index = 0; index < task.totalChunks; index += 1) {
          if (controller.pauseRequested) {
            updateUpload(id, { phase: "paused" });
            return;
          }
          if (controller.uploaded.has(index)) continue;
          const start = index * task.chunkSize;
          const chunk = entry.file.slice(start, Math.min(start + task.chunkSize, entry.file.size));
          await withTransientRetry(() => uploadPdfChunk(task.uploadId, index, chunk));
          controller.uploaded.add(index);
          updateUpload(id, { progress: Math.round((controller.uploaded.size / task.totalChunks) * 100) });
        }
        if (controller.pauseRequested) {
          updateUpload(id, { phase: "paused" });
          return;
        }
        updateUpload(id, {
          phase: "processing",
          processingTask: {
            ...task,
            uploadedChunks: Array.from(controller.uploaded),
            status: "merging",
            progress: 20,
            message: "上传完成，正在合并 PDF 分块",
            elapsedSeconds: 0,
          },
        });
        // 每轮同时读取上传阶段和后台任务，但只在这一条轮询链里合并一次状态。
        // 这样可以保留合并/校验/OCR 的细分进度，又不会让两套独立轮询互相覆盖。
        let lastProgress = 20;
        let activeJob = await withTransientRetry(() => completePdfUpload(task.uploadId));
        const updateJob = (job: BackgroundJob<TextbookImportResult>, uploadStatus?: PdfUploadTask) => {
          lastProgress = Math.max(lastProgress, uploadStatus?.progress ?? 0, job.progress);
          updateUpload(id, {
            processingTask: {
              ...(uploadStatus ?? task),
              jobId: job.jobId,
              jobStatus: job.status,
              attemptCount: job.attemptCount,
              progress: lastProgress,
              message: job.status === "queued" ? job.message : uploadStatus?.message || job.message,
            },
          });
        };
        updateJob(activeJob);
        while (activeJob.status === "queued" || activeJob.status === "running") {
          await new Promise((resolve) => window.setTimeout(resolve, 800));
          const [uploadStatus, nextJob] = await Promise.all([
            withTransientRetry(() => loadPdfUploadStatus(task.uploadId)),
            withTransientRetry(() => loadBackgroundJob<TextbookImportResult>(activeJob.jobId)),
          ]);
          activeJob = nextJob;
          updateJob(activeJob, uploadStatus);
        }
        if (activeJob.status !== "succeeded" || !activeJob.result) {
          throw new Error(activeJob.lastError?.message || activeJob.message || "教材识别失败");
        }
        updateUpload(id, { result: activeJob.result, progress: 100, phase: "done" });
        loadLibrary().then(setLibrary).catch(() => undefined);
      } else {
        if (entry.file.size > IMAGE_MAX_SIZE) throw new Error("单张教材图片不能超过 10 MB");
        updateUpload(id, { phase: "processing", progress: 25 });
        const result = await importTextbook(entry.file, text);
        updateUpload(id, { result, progress: 100, phase: "done" });
      }
    } catch (requestError) {
      updateUpload(id, {
        phase: "error",
        error: requestError instanceof Error ? requestError.message : "教材识别失败",
      });
    } finally {
      running.current.delete(id);
    }
  };

  const upload = async () => {
    const pending = uploadsRef.current.filter((item) => item.phase !== "done" && item.phase !== "processing" && item.phase !== "uploading");
    if (!pending.length) return;
    pending.forEach((item) => updateUpload(item.id, { phase: "queued", error: "" }));
    let cursor = 0;
    const worker = async () => {
      while (cursor < pending.length) {
        const item = pending[cursor];
        cursor += 1;
        await runUpload(item.id);
      }
    };
    await Promise.all(Array.from({ length: Math.min(MAX_CONCURRENT_UPLOADS, pending.length) }, worker));
  };

  const pause = (id: string) => {
    const controller = controllers.current.get(id);
    if (!controller) return;
    controller.pauseRequested = true;
    updateUpload(id, { phase: "paused" });
  };

  const cancelProcessing = async (id: string) => {
    const item = uploadsRef.current.find((entry) => entry.id === id);
    const jobId = item?.processingTask?.jobId;
    if (!jobId) return;
    try {
      const job = await cancelBackgroundJob(jobId);
      updateUpload(id, { processingTask: { ...(item.processingTask as PdfUploadTask), jobStatus: job.status, progress: job.progress, message: job.message } });
    } catch { setGlobalError("取消任务失败，请稍后重试"); }
  };

  const retryProcessing = async (id: string) => {
    const item = uploadsRef.current.find((entry) => entry.id === id);
    const jobId = item?.processingTask?.jobId;
    if (!jobId) return;
    try {
      const job = await retryBackgroundJob<TextbookImportResult>(jobId);
      updateUpload(id, { phase: "processing", error: "", processingTask: { ...(item.processingTask as PdfUploadTask), jobStatus: job.status, progress: job.progress, message: job.message, attemptCount: job.attemptCount } });
      void runUpload(id);
    } catch { setGlobalError("任务重试失败，请稍后重试"); }
  };

  const removeUpload = (id: string) => {
    const item = uploadsRef.current.find((entry) => entry.id === id);
    if (!item || running.current.has(id)) return;
    if (item.preview) URL.revokeObjectURL(item.preview);
    controllers.current.delete(id);
    setUploads((current) => current.filter((entry) => entry.id !== id));
    setActiveUploadId((current) => current === id ? (uploadsRef.current.find((entry) => entry.id !== id)?.id ?? "") : current);
  };

  const runRuntimeSelection = async <T,>(
    target: keyof RuntimeLoadingState,
    request: (signal: AbortSignal) => Promise<T>,
    apply: (result: T) => void,
    errorMessage: string,
  ) => {
    // 同一个下拉连续操作时，取消上一次 UI 请求；否则慢响应可能把新选择覆盖回旧值。
    runtimeRequests.current.get(target)?.abort();
    const controller = new AbortController();
    runtimeRequests.current.set(target, controller);
    setRuntimeLoading((current) => ({ ...current, [target]: true }));
    setGlobalError("");
    try {
      apply(await request(controller.signal));
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) {
        setGlobalError(errorMessage);
      }
    } finally {
      if (runtimeRequests.current.get(target) === controller) {
        runtimeRequests.current.delete(target);
        setRuntimeLoading((current) => ({ ...current, [target]: false }));
      }
    }
  };

  const selectGenerationModel = (provider: ModelProvider, model: string) => runRuntimeSelection(
    "generation",
    (signal) => selectModel(provider, model, signal),
    setModels,
    "模型切换失败",
  );
  const selectTutor = (provider: ModelProvider, model: string) => runRuntimeSelection(
    "tutor",
    (signal) => selectTutorModel(provider, model, signal),
    setTutorModels,
    "陪练模型切换失败",
  );
  const selectOcr = (provider: OcrProvider) => runRuntimeSelection(
    "ocr",
    (signal) => selectOcrProvider(provider, signal),
    setOcrProviders,
    "OCR 切换失败",
  );
  const selectReviewer = (provider: ModelProvider, model: string) => runRuntimeSelection(
    "review",
    (signal) => selectReviewModel(provider, model, signal),
    setReviewModels,
    "审核模型切换失败",
  );

  const openLibraryItem = async (item: LibraryItem) => {
    setLibraryLoadingId(item.uploadId);
    try {
      onOpenLibraryItem(await loadLibraryItem(item.uploadId));
    } catch {
      setGlobalError("教材内容加载失败，请稍后重试");
    } finally { setLibraryLoadingId(""); }
  };
  const removeLibraryItem = async (item: LibraryItem) => {
    if (!window.confirm(`确定从教材库移除「${item.filename}」吗？\n题目和学习记录会保留在库中，之后可以恢复。`)) return;
    setDeletingId(item.uploadId);
    try {
      await deleteLibraryItem(item.uploadId);
      setLibrary((current) => current.filter((entry) => entry.uploadId !== item.uploadId));
    } catch {
      setGlobalError("教材移除失败，请稍后重试");
    } finally { setDeletingId(""); }
  };

  const active = uploads.find((item) => item.id === activeUploadId) ?? uploads[0] ?? null;
  const phase: UploadPhase = uploads.some((item) => item.phase === "uploading")
    ? "uploading"
    : uploads.some((item) => item.phase === "processing")
      ? "processing"
      : uploads.some((item) => item.phase === "queued")
        ? "queued"
        : uploads.some((item) => item.phase === "paused")
          ? "paused"
        : uploads.some((item) => item.phase === "error")
          ? "error"
          : uploads.length > 0 && uploads.every((item) => item.phase === "done") ? "done" : "idle";
  const processingStageIndex = phase === "uploading" || phase === "paused" ? 0 : phase !== "processing" ? -1 : active?.processingTask?.status === "splitting" ? 2 : active?.processingTask?.status === "ocr" ? 3 : active?.processingTask?.status === "generating" ? 4 : 1;

  return {
    uploads,
    activeUploadId: active?.id ?? "",
    activeUpload: active,
    file: active?.file ?? null,
    preview: active?.preview ?? "",
    phase,
    progress: active?.progress ?? 0,
    error: active?.error || globalError,
    result: active?.result ?? null,
    sourceText,
    models,
    tutorModels,
    reviewModels,
    ocrProviders,
    runtimeLoading,
    processingTask: active?.processingTask ?? null,
    library,
    libraryLoadingId,
    deletingId,
    pdfMode: active?.pdfMode ?? false,
    processingStageIndex,
    chooseFiles,
    selectUpload: setActiveUploadId,
    removeUpload,
    setSourceText,
    upload,
    pause,
    cancelProcessing,
    retryProcessing,
    selectGenerationModel,
    selectTutor,
    selectReviewer,
    selectOcr,
    openLibraryItem,
    removeLibraryItem,
  };
}
