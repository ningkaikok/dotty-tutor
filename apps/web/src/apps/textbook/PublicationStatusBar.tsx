import type { PublicationSummary } from "../../types/index";

interface PublicationStatusBarProps {
  publication: PublicationSummary | null;
  publicationBusy: boolean;
  loadingQuestion: boolean;
  fullPaperRunning: boolean;
  fullPaperJobStatus?: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  fullPaperJobCancelRequested?: boolean;
  hasUploadId: boolean;
  hasFullPaper: boolean;
  onGenerateWholePaper: () => void;
  onCancelWholePaper: () => void;
  onSubmitForReview: () => void;
  onRegenerateWholePaper: () => void;
  onPublish: () => void;
}

type StageKey = "draft" | "in_review" | "published";

const STAGES: Array<{ key: StageKey; label: string }> = [
  { key: "draft", label: "草稿" },
  { key: "in_review", label: "审核中" },
  { key: "published", label: "已发布" },
];

/**
 * 发布状态机的独立状态条：三段式进度 + 当前状态下可用的动作按钮。
 * 所有数据与副作用都由 TextbookApp 透传，本组件不持有 state。
 */
export function PublicationStatusBar({
  publication,
  publicationBusy,
  loadingQuestion,
  fullPaperRunning,
  fullPaperJobStatus,
  fullPaperJobCancelRequested,
  hasUploadId,
  hasFullPaper,
  onGenerateWholePaper,
  onCancelWholePaper,
  onSubmitForReview,
  onRegenerateWholePaper,
  onPublish,
}: PublicationStatusBarProps) {
  const currentStage: StageKey = !publication
    ? "draft"
    : publication.status === "in_review"
      ? "in_review"
      : "published";
  const currentIndex = STAGES.findIndex((stage) => stage.key === currentStage);

  return (
    <section className="publication-status-bar panel">
      <ol className="publication-stages">
        {STAGES.map((stage, index) => (
          <li
            key={stage.key}
            className={index === currentIndex ? "current" : index < currentIndex ? "done" : "pending"}
          >
            <span>{stage.label}</span>
          </li>
        ))}
      </ol>

      <div className="publication-actions">
        {hasUploadId && !hasFullPaper && !publication && (
          <button
            className="ghost compact"
            disabled={fullPaperRunning || fullPaperJobStatus === "succeeded"}
            onClick={onGenerateWholePaper}
          >
            {fullPaperJobStatus === "succeeded" ? "整套试卷已生成" : fullPaperJobStatus === "failed" ? "重试生成整套试卷" : "生成整套试卷"}
          </button>
        )}
        {fullPaperRunning && (
          <button className="ghost compact" onClick={onCancelWholePaper}>
            {fullPaperJobCancelRequested ? "正在取消…" : "取消整套生成"}
          </button>
        )}
        {!publication && (
          <button className="ghost compact" disabled={publicationBusy || fullPaperRunning} onClick={onSubmitForReview}>
            {publicationBusy ? "提交中…" : "提交试卷审核"}
          </button>
        )}
        {publication?.status === "in_review" && (
          <>
            <button className="ghost compact" disabled={publicationBusy || loadingQuestion} onClick={onRegenerateWholePaper}>
              {publicationBusy || loadingQuestion ? "生成新版中…" : "整套重新审核"}
            </button>
            <button className="lesson-button" disabled={publicationBusy} onClick={onPublish}>
              {publicationBusy ? "发布中…" : `发布试卷 v${publication.version || 1}`}
            </button>
          </>
        )}
        {publication?.status === "published" && (
          <>
            <button className="ghost compact" disabled={publicationBusy || loadingQuestion} onClick={onRegenerateWholePaper}>
              {publicationBusy || loadingQuestion ? "生成新版中…" : "生成审核新版"}
            </button>
            <span className="active-model live">已发布 v{publication.version || 1}</span>
          </>
        )}
      </div>
    </section>
  );
}
