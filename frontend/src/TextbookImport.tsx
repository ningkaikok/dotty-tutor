import { useEffect, useRef, useState } from "react";
import { completePdfUpload, importTextbook, initPdfUpload, loadLibrary, loadLibraryItem, loadModels, loadOcrProviders, loadPdfUploadStatus, selectModel, selectOcrProvider, uploadPdfChunk } from "./api";
import type { LibraryItem, ModelCatalog, ModelProvider, OcrCatalog, OcrProvider, PdfUploadTask, TextbookImportResult } from "./types";

interface TextbookImportProps {
  onContinue: (result: TextbookImportResult) => void;
}

type UploadPhase = "idle" | "uploading" | "paused" | "processing" | "error" | "done";

const ACCEPTED = "image/*,application/pdf,.heic,.heif";
const PDF_CHUNK_SIZE = 5 * 1024 * 1024;
const IMAGE_MAX_SIZE = 10 * 1024 * 1024;
const PDF_MAX_SIZE = 500 * 1024 * 1024;
const PDF_TAIL_CHECK_SIZE = 64 * 1024;

function formatSize(bytes: number) {
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function isPdf(file: File) {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

async function validatePdfEnvelope(file: File) {
  const header = await file.slice(0, 8).text();
  if (!header.startsWith("%PDF-")) {
    throw new Error("这个文件扩展名是 PDF，但文件头无效。请确认它是真正的 PDF 文件。");
  }

  const tailStart = Math.max(0, file.size - PDF_TAIL_CHECK_SIZE);
  const tail = await file.slice(tailStart).text();
  if (!tail.includes("%%EOF")) {
    throw new Error(
      "这个 PDF 没有正常结束标记（%%EOF），文件可能未下载完整或导出中断。请重新下载，或用“打印 → 存储为 PDF”生成新文件后再上传。",
    );
  }
}

export function TextbookImport({ onContinue }: TextbookImportProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const pdfTaskRef = useRef<{ task: PdfUploadTask; uploaded: Set<number> } | null>(null);
  const pauseRequested = useRef(false);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [dragging, setDragging] = useState(false);
  const [phase, setPhase] = useState<UploadPhase>("idle");
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");
  const [result, setResult] = useState<TextbookImportResult | null>(null);
  const [sourceText, setSourceText] = useState("");
  const [models, setModels] = useState<ModelCatalog | null>(null);
  const [ocrProviders, setOcrProviders] = useState<OcrCatalog | null>(null);
  const [modelLoading, setModelLoading] = useState(false);
  const [processingTask, setProcessingTask] = useState<PdfUploadTask | null>(null);
  const [library, setLibrary] = useState<LibraryItem[]>([]);
  const [libraryLoadingId, setLibraryLoadingId] = useState("");

  useEffect(() => {
    loadModels().then(setModels).catch((modelError) => {
      setError(modelError instanceof Error ? modelError.message : "模型列表加载失败");
    });
    loadOcrProviders().then(setOcrProviders).catch((ocrError) => {
      setError(ocrError instanceof Error ? ocrError.message : "OCR 列表加载失败");
    });
    loadLibrary().then(setLibrary).catch((libraryError) => {
      setError(libraryError instanceof Error ? libraryError.message : "教材库加载失败");
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

    let keepPolling = true;
    const polling = (async () => {
      while (keepPolling) {
        try {
          setProcessingTask(await loadPdfUploadStatus(task.uploadId));
        } catch {
          // 完成请求仍在进行时，短暂的轮询失败不应终止主流程。
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

  const pdfMode = Boolean(file && isPdf(file));
  const pipeline = pdfMode
    ? ["PDF 分块上传", "PDF 合并与校验", "按页规划批次", "首批 5 页 MinerU OCR", "结构化与引导卡"]
    : ["文件校验", "版面与公式识别", "题目结构化", "引导卡生成"];

  const processingStageIndex = (() => {
    if (phase === "uploading" || phase === "paused") return 0;
    if (phase !== "processing") return -1;
    if (processingTask?.status === "splitting") return 2;
    if (processingTask?.status === "ocr") return 3;
    if (processingTask?.status === "generating") return 4;
    return 1;
  })();

  return (
    <main className="import-shell">
      <header className="import-header">
        <div className="brand-mark">D</div>
        <div>
          <strong>Dotty</strong>
          <span>从扫描教材到分步辅导</span>
        </div>
        <span className="demo-badge">LOCAL DEMO</span>
      </header>

      <section className="import-intro">
        <span className="eyebrow">STEP 01 · 教材数字化</span>
        <h1>上传教材页或整本 PDF</h1>
        <p>大 PDF 会按 5 MB 断点上传，后端合并校验后每 5 页规划一个识别批次。</p>
      </section>

      <section className="model-switcher panel">
        <div>
          <span className="eyebrow">MODEL RUNTIME</span>
          <strong>选择实际生成模型</strong>
          <small>默认优先本地模型；切换后会影响教材脚本和 Help 回答。</small>
        </div>
        <select
          value={models ? `${models.selected.provider}::${models.selected.model}` : ""}
          disabled={!models || modelLoading || phase === "uploading" || phase === "processing"}
          onChange={async (event) => {
            const [provider, model] = event.target.value.split("::") as [ModelProvider, string];
            setModelLoading(true);
            setError("");
            try {
              setModels(await selectModel(provider, model));
            } catch (modelError) {
              setError(modelError instanceof Error ? modelError.message : "模型切换失败");
            } finally {
              setModelLoading(false);
            }
          }}
        >
          {!models && <option>正在读取模型…</option>}
          {models?.providers.flatMap((provider) =>
            provider.models.map((model) => (
              <option
                key={`${provider.id}::${model}`}
                value={`${provider.id}::${model}`}
                disabled={!provider.available}
              >
                {provider.label} · {model}
              </option>
            )),
          )}
        </select>
        {models && (
          <span className={`runtime-status ${models.selected.provider}`}>
            <i /> {models.providers.find((item) => item.id === models.selected.provider)?.detail}
          </span>
        )}
        <div className="ocr-label">
          <strong>选择教材解析方式</strong>
          <small>MinerU 输出 Markdown、公式 LaTeX 和结构化内容，随后交给模型生成课程。</small>
        </div>
        <select
          className="ocr-select"
          value={ocrProviders?.selected ?? ""}
          disabled={!ocrProviders || modelLoading || phase === "uploading" || phase === "processing"}
          onChange={async (event) => {
            setModelLoading(true);
            setError("");
            try {
              setOcrProviders(await selectOcrProvider(event.target.value as OcrProvider));
            } catch (ocrError) {
              setError(ocrError instanceof Error ? ocrError.message : "OCR 切换失败");
            } finally {
              setModelLoading(false);
            }
          }}
        >
          {!ocrProviders && <option>正在读取 OCR…</option>}
          {ocrProviders?.providers.map((provider) => (
            <option key={provider.id} value={provider.id} disabled={!provider.available}>
              {provider.label}{provider.available ? "" : " · 未安装"}
            </option>
          ))}
        </select>
        {ocrProviders && (
          <span className={`runtime-status ocr-status ${ocrProviders.effective}`}>
            <i /> 当前实际解析：{ocrProviders.effective} · {ocrProviders.providers.find((item) => item.id === ocrProviders.effective)?.detail}
          </span>
        )}
      </section>

      {library.length > 0 && (
        <section className="library-panel panel">
          <div className="library-heading">
            <div>
              <span className="eyebrow">TEXTBOOK LIBRARY</span>
              <strong>已持久化教材</strong>
              <small>PDF、处理状态和生成题目已保存在本机，重启后仍可继续。</small>
            </div>
            <span>{library.length} 本</span>
          </div>
          <div className="library-list">
            {library.map((item) => (
              <button
                key={item.uploadId}
                disabled={Boolean(libraryLoadingId)}
                onClick={async () => {
                  setLibraryLoadingId(item.uploadId);
                  setError("");
                  try {
                    onContinue(await loadLibraryItem(item.uploadId));
                  } catch (libraryError) {
                    setError(libraryError instanceof Error ? libraryError.message : "教材打开失败");
                  } finally {
                    setLibraryLoadingId("");
                  }
                }}
              >
                <span className="library-pdf">PDF</span>
                <span>
                  <strong>{item.filename}</strong>
                  <small>{item.chapter} · {item.pageCount ?? "?"} 页 · {item.questionCount} 道题</small>
                </span>
                <b>{libraryLoadingId === item.uploadId ? "读取中…" : "继续学习 →"}</b>
              </button>
            ))}
          </div>
        </section>
      )}

      <section className="import-grid">
        <div
          className={`upload-dropzone panel ${dragging ? "dragging" : ""}`}
          onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            chooseFile(event.dataTransfer.files[0]);
          }}
        >
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPTED}
            onChange={(event) => chooseFile(event.target.files?.[0])}
            hidden
          />

          {file ? (
            <div className="file-preview">
              {preview ? <img src={preview} alt="教材页预览" /> : <div className="pdf-preview">PDF</div>}
              <div className="file-details">
                <span className="file-type">{pdfMode ? "整本 PDF 教材" : "扫描教材页"}</span>
                <strong>{file.name}</strong>
                <small>
                  {formatSize(file.size)} · {pdfMode ? "5 MB 分块，支持暂停续传" : "文件仅在内存中处理"}
                </small>
                {phase !== "uploading" && phase !== "processing" && (
                  <button className="text-button" onClick={() => inputRef.current?.click()}>更换文件</button>
                )}
              </div>
            </div>
          ) : (
            <button className="dropzone-button" onClick={() => inputRef.current?.click()}>
              <span className="upload-icon">↥</span>
              <strong>拖入教材页或整本 PDF</strong>
              <small>图片最大 10 MB · PDF 最大 500 MB</small>
            </button>
          )}

          <label className="source-text-field">
            <span>题目原文 <small>可选，但扫描图片测试本地文本模型时建议填写</small></span>
            <textarea
              value={sourceText}
              onChange={(event) => setSourceText(event.target.value)}
              disabled={phase === "uploading" || phase === "processing"}
              placeholder="例如：已知 A、B 是两个定点，点 P 满足 PA = PB……\n带文字层的 PDF 可以留空，系统会自动抽取前 10 页文本。"
            />
          </label>

          {!result && phase === "uploading" && (
            <div className="upload-progress-block">
              <div><span>正在上传 PDF 分块</span><strong>{progress}%</strong></div>
              <div className="progress-track"><i style={{ width: `${progress}%` }} /></div>
              <button
                className="pause-button"
                onClick={() => { pauseRequested.current = true; setPhase("paused"); }}
              >暂停上传</button>
            </div>
          )}

          {!result && phase === "processing" && pdfMode && (
            <div className="processing-progress-card" aria-live="polite">
              <div className="processing-progress-heading">
                <span>{processingTask?.message ?? "正在准备 PDF 处理任务"}</span>
                <strong>{processingTask?.progress ?? 20}%</strong>
              </div>
              <div className="progress-track processing-track">
                <i style={{ width: `${processingTask?.progress ?? 20}%` }} />
              </div>
              <small>
                已耗时 {Math.round(processingTask?.elapsedSeconds ?? 0)} 秒
                {processingTask?.status === "ocr"
                  ? " · 当前只识别首批 5 页；本机 OCR 通常需 20–60 秒，首次预热可能更久"
                  : processingTask?.status === "generating"
                    ? " · OCR 已完成，正在由本地模型生成课程"
                  : " · 页面可保持打开，状态每秒自动更新"}
              </small>
            </div>
          )}

          {!result && phase !== "uploading" && (
            <button
              className="import-button"
              disabled={!file || phase === "processing"}
              onClick={upload}
            >
              {phase === "paused"
                ? `继续上传 · ${progress}%`
                : phase === "processing"
                  ? (pdfMode ? (processingTask?.message ?? "正在处理 PDF…") : "正在识别版面、公式与题目…")
                  : phase === "error" && pdfTaskRef.current
                    ? `从 ${progress}% 重试`
                    : "开始数字化"}
            </button>
          )}
          {error && <p className="import-error">{error}</p>}
        </div>

        <aside className="pipeline-panel panel">
          <span className="eyebrow">PROCESS</span>
          <h2>{result ? "教材已拆分并结构化" : "即将执行的处理链路"}</h2>
          <ol className="pipeline-list">
            {(result?.stages.map((stage) => stage.label) ?? pipeline).map((label, index) => {
              const activeIndex = processingStageIndex;
              const complete = Boolean(result) || (activeIndex > 0 && index < activeIndex);
              return (
                <li key={label} className={complete ? "complete" : index === activeIndex ? "active" : "pending"}>
                  <span>{complete ? "✓" : index + 1}</span>{label}
                </li>
              );
            })}
          </ol>

          {result ? (
            <div className="extraction-result">
              <div className={`model-result ${result.modelRun.fallback ? "fallback" : "live"}`}>
                <small>实际生成</small>
                <strong>{result.modelRun.provider} · {result.modelRun.model}</strong>
                {result.modelRun.fallback && (
                  <span title={result.modelRun.error}>模型调用失败，已回退 Mock · {result.modelRun.error}</span>
                )}
              </div>
              <div className={`model-result ${result.ocrRun.fallback ? "fallback" : "live"}`}>
                <small>教材解析</small>
                <strong>{result.ocrRun.provider} · {result.ocrRun.mode}</strong>
                {result.ocrRun.error && <span title={result.ocrRun.error}>{result.ocrRun.error}</span>}
              </div>
              <div><small>识别章节</small><strong>{result.extraction.chapter}</strong></div>
              <div><small>知识点</small><strong>{result.extraction.knowledgePoint}</strong></div>
              <div className="metric-row">
                {result.extraction.pageCount ? <span><b>{result.extraction.pageCount}</b> 页</span> : null}
                {result.extraction.batchCount ? <span><b>{result.extraction.batchCount}</b> 个批次</span> : null}
                <span><b>{result.extraction.questionCount}</b> 道题</span>
                <span><b>{result.extraction.guideCardCount}</b> 张引导卡</span>
              </div>
              {result.batches && (
                <div className="batch-list">
                  <small>PDF 处理批次</small>
                  {result.batches.map((batch) => (
                    <span key={batch.id}>
                      第 {batch.startPage}-{batch.endPage} 页
                      <b className={batch.status}>{batch.status === "processed" ? "已处理" : "按需处理"}</b>
                    </span>
                  ))}
                </div>
              )}
              <button className="continue-button" onClick={() => onContinue(result)}>进入动态教材 →</button>
            </div>
          ) : (
            <p className="pipeline-note">
              {pdfMode
                ? phase === "processing"
                  ? (processingTask?.message ?? "正在读取后端处理进度…")
                  : "上传中断后点击继续，只会补传缺失分块；整本书先规划页码批次，只生成并识别首批 5 页。"
                : "图片会直接进入版面、公式和题目识别流程。"}
            </p>
          )}
        </aside>
      </section>
    </main>
  );
}
