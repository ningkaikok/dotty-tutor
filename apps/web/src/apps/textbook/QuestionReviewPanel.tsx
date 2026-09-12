import { useEffect, useState } from "react";
import { loadQuestionReview, rerunQuestionStage } from "../../api/textbooks";
import type { QuestionPayload } from "../../types/question";
import type { QuestionReviewItem } from "../../types/textbook";

const STAGES = [
  ["extraction", "重新抽取"],
  ["solution", "重新求解"],
  ["verification", "重新核验"],
  ["tutor-script", "重新生成讲解"],
] as const;

interface Props {
  uploadId?: string;
  payload: QuestionPayload;
  onPayload: (payload: QuestionPayload) => void;
}

export function QuestionReviewPanel({ uploadId, payload, onPayload }: Props) {
  const [review, setReview] = useState<QuestionReviewItem | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const sourceKey = payload.question.sourceQuestionKey;

  useEffect(() => {
    if (!uploadId || !sourceKey) return;
    let active = true;
    void loadQuestionReview(uploadId, sourceKey)
      .then((result) => active && setReview(result))
      .catch((requestError) => active && setError(requestError instanceof Error ? requestError.message : "审核信息加载失败"));
    return () => { active = false; };
  }, [uploadId, sourceKey]);

  if (!uploadId || !sourceKey) return null;
  const provenance = review?.provenance || payload.question.sourceProvenance || {};
  const issues = review?.issues || [];
  const rerun = async (stage: typeof STAGES[number][0]) => {
    setBusy(stage);
    setError("");
    try {
      const result = await rerunQuestionStage(uploadId, sourceKey, stage);
      onPayload(result.questionPayload);
      setReview(await loadQuestionReview(uploadId, sourceKey));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "阶段重跑失败");
    } finally {
      setBusy("");
    }
  };

  return (
    <aside className="question-review-panel" aria-label="题目审核证据">
      <div className="question-review-heading">
        <strong>来源与阶段审核</strong>
        <span>{String(provenance.sourceOrigin || "unknown")}</span>
      </div>
      <div className="question-review-meta">
        页码：{Array.isArray(provenance.sourcePages) ? provenance.sourcePages.join(", ") : "—"} ·
        块：{Array.isArray(provenance.sourceBlockIds) ? provenance.sourceBlockIds.length : 0} ·
        置信度：{typeof provenance.confidence === "number" ? provenance.confidence : "—"}
      </div>
      {issues.length > 0 && <ul className="question-review-issues">{issues.map((issue) => <li key={`${issue.code}-${issue.message}`}>{issue.code}：{issue.message}</li>)}</ul>}
      {error && <p className="question-review-error" role="alert">{error}</p>}
      <div className="question-review-actions">
        {STAGES.map(([stage, label]) => <button key={stage} type="button" disabled={Boolean(busy)} onClick={() => void rerun(stage)}>{busy === stage ? "处理中…" : label}</button>)}
      </div>
    </aside>
  );
}
