import { useRef, useState } from "react";
import type { PdfUploadTask, TextbookImportResult } from "../../../types";
import { ACCEPTED_TEXTBOOK_FILES, formatFileSize } from "./fileValidation";
import type { UploadPhase } from "./useTextbookImport";

interface UploadPanelProps {
  file: File | null;
  preview: string;
  phase: UploadPhase;
  progress: number;
  error: string;
  result: TextbookImportResult | null;
  sourceText: string;
  pdfMode: boolean;
  processingTask: PdfUploadTask | null;
  onChooseFile: (file?: File) => void;
  onSourceTextChange: (value: string) => void;
  onUpload: () => void;
  onPause: () => void;
}

export function UploadPanel({
  file,
  preview,
  phase,
  progress,
  error,
  result,
  sourceText,
  pdfMode,
  processingTask,
  onChooseFile,
  onSourceTextChange,
  onUpload,
  onPause,
}: UploadPanelProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const busy = phase === "uploading" || phase === "processing";

  return (
    <div
      className={`upload-dropzone panel ${dragging ? "dragging" : ""}`}
      onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        onChooseFile(event.dataTransfer.files[0]);
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED_TEXTBOOK_FILES}
        onChange={(event) => onChooseFile(event.target.files?.[0])}
        hidden
      />

      {file ? (
        <div className="file-preview">
          {preview ? <img src={preview} alt="教材页预览" /> : <div className="pdf-preview">PDF</div>}
          <div className="file-details">
            <span className="file-type">{pdfMode ? "整本 PDF 教材" : "扫描教材页"}</span>
            <strong>{file.name}</strong>
            <small>
              {formatFileSize(file.size)} · {pdfMode ? "5 MB 分块，支持暂停续传" : "文件仅在内存中处理"}
            </small>
            {!busy && <button className="text-button" onClick={() => inputRef.current?.click()}>更换文件</button>}
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
          onChange={(event) => onSourceTextChange(event.target.value)}
          disabled={busy}
          placeholder="例如：已知 A、B 是两个定点，点 P 满足 PA = PB……\n带文字层的 PDF 可以留空，系统会自动抽取前 10 页文本。"
        />
      </label>

      {!result && phase === "uploading" && (
        <div className="upload-progress-block">
          <div><span>正在上传 PDF 分块</span><strong>{progress}%</strong></div>
          <div className="progress-track"><i style={{ width: `${progress}%` }} /></div>
          <button className="pause-button" onClick={onPause}>暂停上传</button>
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
        <button className="import-button" disabled={!file || phase === "processing"} onClick={onUpload}>
          {phase === "paused"
            ? `继续上传 · ${progress}%`
            : phase === "processing"
              ? (pdfMode ? (processingTask?.message ?? "正在处理 PDF…") : "正在识别版面、公式与题目…")
              : phase === "error"
                ? `重新尝试 · ${progress}%`
                : "开始数字化"}
        </button>
      )}
      {error && <p className="import-error" role="alert">{error}</p>}
    </div>
  );
}
