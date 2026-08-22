import type { PdfUploadTask, TextbookImportResult } from "../../../types/index";
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

export function PipelinePanel({ result, pdfMode, phase, processingTask, activeStage, onContinue }: PipelinePanelProps) {
  const pipeline = pdfMode ? PDF_PIPELINE : IMAGE_PIPELINE;

  return (
    <aside className="pipeline-panel panel">
      <span className="eyebrow">PROCESS</span>
      <h2>{result ? "教材已拆分并结构化" : "即将执行的处理链路"}</h2>
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
          <button className="continue-button" onClick={() => onContinue(result)}>进入动态教材 →</button>
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
