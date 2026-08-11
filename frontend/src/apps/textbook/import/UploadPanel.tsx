import { useRef, useState } from "react";
import { ACCEPTED_TEXTBOOK_FILES, formatFileSize } from "./fileValidation";
import type { TextbookUploadItem, UploadPhase } from "./useTextbookImport";

interface UploadPanelProps {
  uploads: TextbookUploadItem[];
  activeUploadId: string;
  phase: UploadPhase;
  error: string;
  sourceText: string;
  onChooseFiles: (files: FileList | File[]) => void;
  onSelectUpload: (id: string) => void;
  onRemoveUpload: (id: string) => void;
  onSourceTextChange: (value: string) => void;
  onUpload: () => void;
  onPause: (id: string) => void;
}

const phaseLabels: Record<UploadPhase, string> = {
  idle: "待开始",
  queued: "排队中",
  uploading: "上传中",
  paused: "已暂停",
  processing: "识别中",
  error: "失败",
  done: "已完成",
};

function itemProgress(item: TextbookUploadItem): number {
  return item.phase === "processing" ? item.processingTask?.progress ?? 20 : item.progress;
}

function itemMessage(item: TextbookUploadItem): string {
  if (item.error) return item.error;
  if (item.phase === "processing") return item.processingTask?.message ?? "正在准备识别任务";
  if (item.phase === "uploading") return "正在上传 PDF 分块";
  if (item.phase === "queued") return "等待可用的本地 OCR / 模型资源";
  if (item.phase === "done") return "识别完成，可在右侧查看结果";
  return phaseLabels[item.phase];
}

export function UploadPanel({
  uploads,
  activeUploadId,
  phase,
  error,
  sourceText,
  onChooseFiles,
  onSelectUpload,
  onRemoveUpload,
  onSourceTextChange,
  onUpload,
  onPause,
}: UploadPanelProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const busy = phase === "uploading" || phase === "processing";
  const pendingCount = uploads.filter((item) => item.phase !== "done").length;

  return (
    <div
      className={`upload-dropzone panel ${dragging ? "dragging" : ""}`}
      onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        onChooseFiles(event.dataTransfer.files);
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED_TEXTBOOK_FILES}
        multiple
        onChange={(event) => {
          onChooseFiles(event.target.files ?? []);
          event.currentTarget.value = "";
        }}
        hidden
      />

      <button className="dropzone-button" onClick={() => inputRef.current?.click()}>
        <span className="upload-icon">↥</span>
        <strong>拖入或选择多个教材 PDF</strong>
        <small>支持同时加入多个文件 · PDF 最大 500 MB · 图片仍可单张识别</small>
      </button>

      {uploads.length > 0 && (
        <div className="upload-list" aria-live="polite">
          <div className="upload-list-heading">
            <strong>识别队列</strong>
            <span>{uploads.length} 个文件 · {uploads.filter((item) => item.phase === "done").length} 个完成</span>
          </div>
          {uploads.map((item) => {
            const progress = itemProgress(item);
            const selected = item.id === activeUploadId;
            const itemBusy = item.phase === "uploading" || item.phase === "processing";
            return (
              <article className={`upload-item ${selected ? "selected" : ""} ${item.phase}`} key={item.id}>
                <button className="upload-item-main" onClick={() => onSelectUpload(item.id)}>
                  {item.preview ? <img src={item.preview} alt="教材页缩略图" /> : <span className="upload-file-icon">PDF</span>}
                  <span className="upload-item-copy">
                    <strong title={item.file.name}>{item.file.name}</strong>
                    <small>{formatFileSize(item.file.size)} · {phaseLabels[item.phase]}</small>
                    <span className="upload-item-message">{itemMessage(item)}</span>
                  </span>
                </button>
                <div className="upload-item-progress">
                  <div className="processing-progress-heading"><span>{item.pdfMode ? "PDF 处理" : "图片识别"}</span><strong>{progress}%</strong></div>
                  <div className="progress-track"><i style={{ width: `${progress}%` }} /></div>
                  {item.processingTask?.elapsedSeconds ? <small>已耗时 {Math.round(item.processingTask.elapsedSeconds)} 秒</small> : null}
                </div>
                <div className="upload-item-actions">
                  {itemBusy && item.phase === "uploading" && <button className="text-button" onClick={() => onPause(item.id)}>暂停</button>}
                  {!itemBusy && item.phase !== "done" && <button className="text-button" onClick={() => onRemoveUpload(item.id)}>移除</button>}
                  {item.phase === "done" && <span className="upload-done-mark">✓</span>}
                </div>
              </article>
            );
          })}
        </div>
      )}

      <label className="source-text-field">
        <span>题目原文 <small>可选；会作为所有新任务的补充文本</small></span>
        <textarea
          value={sourceText}
          onChange={(event) => onSourceTextChange(event.target.value)}
          disabled={busy}
          placeholder="例如：已知 A、B 是两个定点，点 P 满足 PA = PB……"
        />
      </label>

      <button className="import-button" disabled={!pendingCount || busy} onClick={onUpload}>
        {busy ? `正在处理 ${uploads.filter((item) => itemBusy(item)).length} 个任务…` : pendingCount < uploads.length ? `继续未完成的 ${pendingCount} 个文件` : `开始识别 ${pendingCount} 个文件`}
      </button>
      {error && <p className="import-error" role="alert">{error}</p>}
    </div>
  );
}

function itemBusy(item: TextbookUploadItem): boolean {
  return item.phase === "uploading" || item.phase === "processing";
}
