import { useEffect, useRef, useState } from "react";
import {
  completePdfUpload,
  deleteLibraryItem,
  importTextbook,
  initPdfUpload,
  loadLibrary,
  loadLibraryItem,
  loadModels,
  loadOcrProviders,
  loadReviewModels,
  loadPdfUploadStatus,
  selectModel,
  selectOcrProvider,
  selectReviewModel,
  uploadPdfChunk,
} from "../../../api";
import type {
  LibraryItem,
  ModelCatalog,
  ModelProvider,
  OcrCatalog,
  OcrProvider,
  PdfUploadTask,
  ReviewModelCatalog,
  TextbookImportResult,
} from "../../../types";
import {
  IMAGE_MAX_SIZE,
  PDF_CHUNK_SIZE,
  PDF_MAX_SIZE,
  isPdf,
  validatePdfEnvelope,
} from "./fileValidation";

export type UploadPhase = "idle" | "uploading" | "paused" | "processing" | "error" | "done";

interface UseTextbookImportOptions {
  onOpenLibraryItem: (result: TextbookImportResult) => void;
}

/**
 * 教材导入状态机和全部 API 副作用的唯一拥有者。
 *
 * 展示组件只接收普通状态和回调，因此调整页面布局不会破坏断点上传。大文件控制数据放在 ref，
 * 用户可见快照放在 state；这样每个分块完成时既不会重建控制器，又能更新进度条。
 */
export function useTextbookImport({ onOpenLibraryItem }: UseTextbookImportOptions) {
  // Ref 保存跨渲染的可变上传控制数据，state 仍是界面显示的真相来源。
  const pdfTaskRef = useRef<{ task: PdfUploadTask; uploaded: Set<number> } | null>(null);
  const pauseRequested = useRef(false);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [phase, setPhase] = useState<UploadPhase>("idle");
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");
  const [result, setResult] = useState<TextbookImportResult | null>(null);
  const [sourceText, setSourceText] = useState("");
  const [models, setModels] = useState<ModelCatalog | null>(null);
  const [reviewModels, setReviewModels] = useState<ReviewModelCatalog | null>(null);
  const [ocrProviders, setOcrProviders] = useState<OcrCatalog | null>(null);
  const [runtimeLoading, setRuntimeLoading] = useState(false);
  const [processingTask, setProcessingTask] = useState<PdfUploadTask | null>(null);
  const [library, setLibrary] = useState<LibraryItem[]>([]);
  const [libraryLoadingId, setLibraryLoadingId] = useState("");
  const [deletingId, setDeletingId] = useState("");

  useEffect(() => {
    loadModels().then(setModels).catch((requestError) => {
      setError(requestError instanceof Error ? requestError.message : "模型列表加载失败");
    });
    loadOcrProviders().then(setOcrProviders).catch((requestError) => {
      setError(requestError instanceof Error ? requestError.message : "OCR 列表加载失败");
    });
    loadReviewModels().then(setReviewModels).catch((requestError) => {
      setError(requestError instanceof Error ? requestError.message : "审核模型列表加载失败");
    });
    loadLibrary().then(setLibrary).catch((requestError) => {
      setError(requestError instanceof Error ? requestError.message : "教材库加载失败");
    });
  }, []);

  useEffect(() => {
    if (!file || !file.type.startsWith("image/")) {
      setPreview("");
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const chooseFile = (nextFile?: File) => {
    if (!nextFile) return;
    setFile(nextFile);
    setResult(null);
    setError("");
    setProgress(0);
    setPhase("idle");
    setProcessingTask(null);
    pauseRequested.current = false;
    pdfTaskRef.current = null;
  };

  const runPdfUpload = async (pdf: File) => {
    if (pdf.size > PDF_MAX_SIZE) throw new Error("PDF 不能超过 500 MB");
    let current = pdfTaskRef.current;
    if (!current) {
      const task = await initPdfUpload(pdf, PDF_CHUNK_SIZE, sourceText);
      current = { task, uploaded: new Set(task.uploadedChunks) };
      pdfTaskRef.current = current;
    }

    pauseRequested.current = false;
    setPhase("uploading");
    const { task, uploaded } = current;
    // Set 来源于服务端断点状态。跳过已存在索引后，同一循环即可覆盖首次上传、暂停恢复和刷新恢复。
    for (let index = 0; index < task.totalChunks; index += 1) {
      if (pauseRequested.current) return;
      if (uploaded.has(index)) continue;
      const start = index * task.chunkSize;
      const chunk = pdf.slice(start, Math.min(start + task.chunkSize, pdf.size));
      await uploadPdfChunk(task.uploadId, index, chunk);
      uploaded.add(index);
      setProgress(Math.round((uploaded.size / task.totalChunks) * 100));
    }
    if (pauseRequested.current) return;

    setPhase("processing");
    setProcessingTask({
      ...task,
      uploadedChunks: Array.from(uploaded),
      status: "merging",
      progress: 20,
      message: "上传完成，正在合并 PDF 分块",
      elapsedSeconds: 0,
    });

    // 当前 completion 请求同步执行耗时工作，轮询只是只读地刷新进度卡。未来改成 Worker 后可
    // 删除这个伴随轮询，其余状态机和 UI 契约无需改变。
    let keepPolling = true;
    const polling = (async () => {
      while (keepPolling) {
        try {
          setProcessingTask(await loadPdfUploadStatus(task.uploadId));
        } catch {
          // 状态查询短暂失败不应取消仍在执行的 completion 请求。
        }
        await new Promise((resolve) => window.setTimeout(resolve, 800));
      }
    })();

    try {
      const completed = await completePdfUpload(task.uploadId);
      setResult(completed);
      loadLibrary().then(setLibrary).catch(() => undefined);
      setProgress(100);
      setPhase("done");
    } finally {
      keepPolling = false;
      await polling;
    }
  };

  const upload = async () => {
    if (!file || phase === "uploading" || phase === "processing") return;
    setError("");
    try {
      if (isPdf(file)) {
        await validatePdfEnvelope(file);
        await runPdfUpload(file);
      } else {
        if (file.size > IMAGE_MAX_SIZE) throw new Error("单张教材图片不能超过 10 MB");
        setPhase("processing");
        setResult(await importTextbook(file, sourceText));
        setProgress(100);
        setPhase("done");
      }
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "教材识别失败");
      setPhase("error");
    }
  };

  const selectGenerationModel = async (provider: ModelProvider, model: string) => {
    setRuntimeLoading(true);
    setError("");
    try {
      setModels(await selectModel(provider, model));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "模型切换失败");
    } finally {
      setRuntimeLoading(false);
    }
  };

  const selectOcr = async (provider: OcrProvider) => {
    setRuntimeLoading(true);
    setError("");
    try {
      setOcrProviders(await selectOcrProvider(provider));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "OCR 切换失败");
    } finally {
      setRuntimeLoading(false);
    }
  };

  const selectReviewer = async (provider: ModelProvider, model: string) => {
    setRuntimeLoading(true);
    setError("");
    try {
      setReviewModels(await selectReviewModel(provider, model));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "审核模型切换失败");
    } finally {
      setRuntimeLoading(false);
    }
  };

  const openLibraryItem = async (item: LibraryItem) => {
    setLibraryLoadingId(item.uploadId);
    setError("");
    try {
      onOpenLibraryItem(await loadLibraryItem(item.uploadId));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "教材打开失败");
    } finally {
      setLibraryLoadingId("");
    }
  };

  const removeLibraryItem = async (item: LibraryItem) => {
    if (!window.confirm(`确定从教材库移除「${item.filename}」吗？\n题目和学习记录会保留在库中，之后可以恢复。`)) return;
    setDeletingId(item.uploadId);
    setError("");
    try {
      await deleteLibraryItem(item.uploadId);
      setLibrary((current) => current.filter((entry) => entry.uploadId !== item.uploadId));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "教材删除失败");
    } finally {
      setDeletingId("");
    }
  };

  const pdfMode = Boolean(file && isPdf(file));
  const processingStageIndex = phase === "uploading" || phase === "paused"
    ? 0
    : phase !== "processing"
      ? -1
      : processingTask?.status === "splitting"
        ? 2
        : processingTask?.status === "ocr"
          ? 3
          : processingTask?.status === "generating"
            ? 4
            : 1;

  return {
    file,
    preview,
    phase,
    progress,
    error,
    result,
    sourceText,
    models,
    reviewModels,
    ocrProviders,
    runtimeLoading,
    processingTask,
    library,
    libraryLoadingId,
    deletingId,
    pdfMode,
    processingStageIndex,
    chooseFile,
    setSourceText,
    upload,
    pause: () => { pauseRequested.current = true; setPhase("paused"); },
    selectGenerationModel,
    selectReviewer,
    selectOcr,
    openLibraryItem,
    removeLibraryItem,
  };
}
