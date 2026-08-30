import type { ImportQualityReport, PdfUploadTask, TextbookImportResult } from "../../../types/index";
import type { UploadPhase } from "./useTextbookImport";

interface PipelinePanelProps {
  result: TextbookImportResult | null;
  pdfMode: boolean;
  phase: UploadPhase;
  processingTask: PdfUploadTask | null;
  activeStage: number;
  onContinue: (result: TextbookImportResult) => void;
}

const IMAGE_PIPELINE = ["文件校验", "版面与公式识别", "题目结构化", "引导卡生成"];
const PDF_PIPELINE = ["PDF 分块上传", "PDF 合并与校验", "按页规划批次", "页面级 OCR 与局部重试", "结构化与引导卡"];

function QualityReport({ report }: { report: ImportQualityReport }) {
  const statusLabel = report.status === "ready" ? "可执行整本生成" : report.status === "warning" ? "有警告，可继续" : "已暂停整本生成";
  return (
    <section className={`import-quality-report ${report.status}`} aria-live="polite">
      <div className="quality-report-heading">
        <strong>导入质量报告</strong>
        <b>{statusLabel}</b>
      </div>
      <div className="quality-report-metrics">
        <span>预计题目 <b>{report.expectedQuestionCount}</b></span>
        <span>题号范围 <b>{report.questionRange}</b></span>
        <span>重复题号 <b>{report.duplicateQuestionNumbers.length}</b></span>
        <span>未识别页 <b>{report.unidentifiedPages.length}</b></span>
        <span>图片冲突 <b>{report.imageAttributionConflicts.length}</b></span>
      </div>
      {(report.blockers.length > 0 || report.warnings.length > 0) && (
        <div className="quality-report-messages">
          {report.blockers.map((message) => <p key={`blocker-${message}`}>⛔ {message}</p>)}
          {report.warnings.map((message) => <p key={`warning-${message}`}>⚠ {message}</p>)}
        </div>
      )}
      <small>已检查 {report.checkedBatchCount} 个批次 · {report.scope === "full-paper" ? "整本 OCR" : "首批预览"}</small>
    </section>
  );
}

export function PipelinePanel({ result, pdfMode, phase, processingTask, activeStage, onContinue }: PipelinePanelProps) {
  const pipeline = pdfMode ? PDF_PIPELINE : IMAGE_PIPELINE;
  const canContinue = Boolean(result) && (phase === "done" || !pdfMode);

  return (
    <aside className="pipeline-panel panel">
      <span className="eyebrow">处理链路</span>
      <h2>{result ? (phase === "done" ? "教材已拆分并结构化" : "预览已完成，整本处理中") : "即将执行的处理链路"}</h2>
      <ol className="pipeline-list">
        {(result?.stages.map((stage) => stage.label) ?? pipeline).map((label, index) => {
          const complete = Boolean(result) || (activeStage > 0 && index < activeStage);
          return (
            <li key={label} className={complete ? "complete" : index === activeStage ? "active" : "pending"}>
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
            {result.modelRun.fallback && <span title={result.modelRun.error}>模型调用失败，已回退 Mock · {result.modelRun.error}</span>}
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
          {result.qualityReport && <QualityReport report={result.qualityReport} />}
          <button
            className="continue-button"
            disabled={!canContinue}
            onClick={() => canContinue && onContinue(result)}
          >
            {canContinue ? "进入动态教材 →" : "整本教材处理中…"}
          </button>
        </div>
      ) : (
        <p className="pipeline-note">
          {pdfMode
            ? phase === "processing"
              ? (processingTask?.message ?? "正在读取后端处理进度…")
              : "上传中断后点击继续，只会补传缺失分块；上传完成后由后台按页码批次持续识别整本教材。"
            : "图片会直接进入版面、公式和题目识别流程。"}
        </p>
      )}
    </aside>
  );
}
