import { useEffect, useState } from "react";
import {
  activateQuestionRevision,
  editQuestion,
  listQuestionRevisions,
  loadQuestionReview,
  rerunQuestionStage,
} from "../../api/textbooks";
import type { QuestionPayload } from "../../types/question";
import type { QuestionReviewItem, RevisionSummary } from "../../types/textbook";

const STAGES = [
  ["extraction", "重新抽取"],
  ["solution", "重新求解"],
  ["verification", "重新核验"],
  ["tutor-script", "重新生成讲解"],
] as const;

const REVISION_SOURCE_LABEL: Record<string, string> = {
  manual_edit: "人工编辑",
  model_generated: "模型生成",
};

interface Props {
  uploadId?: string;
  payload: QuestionPayload;
  onPayload: (payload: QuestionPayload) => void;
}

export function QuestionReviewPanel({ uploadId, payload, onPayload }: Props) {
  const [review, setReview] = useState<QuestionReviewItem | null>(null);
  const [revisions, setRevisions] = useState<RevisionSummary[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [promptDraft, setPromptDraft] = useState("");
  const [correctAnswerDraft, setCorrectAnswerDraft] = useState("");
  const sourceKey = payload.question.sourceQuestionKey;

  useEffect(() => {
    if (!uploadId || !sourceKey) return;
    let active = true;
    Promise.all([loadQuestionReview(uploadId, sourceKey), listQuestionRevisions(uploadId, sourceKey)])
      .then(([nextReview, nextRevisions]) => {
        if (!active) return;
        setReview(nextReview);
        setRevisions(nextRevisions);
      })
      .catch((requestError) => active && setError(requestError instanceof Error ? requestError.message : "审核信息加载失败"));
    return () => { active = false; };
  }, [uploadId, sourceKey]);

  if (!uploadId || !sourceKey) return null;
  const provenance = review?.provenance || payload.question.sourceProvenance || {};
  const issues = review?.issues || [];

  const refreshReviewAndRevisions = async () => {
    const [nextReview, nextRevisions] = await Promise.all([
      loadQuestionReview(uploadId, sourceKey),
      listQuestionRevisions(uploadId, sourceKey),
    ]);
    setReview(nextReview);
    setRevisions(nextRevisions);
  };

  const rerun = async (stage: typeof STAGES[number][0]) => {
    setBusy(stage);
    setError("");
    try {
      const result = await rerunQuestionStage(uploadId, sourceKey, stage);
      onPayload(result.questionPayload);
      await refreshReviewAndRevisions();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "阶段重跑失败");
    } finally {
      setBusy("");
    }
  };

  const beginEdit = () => {
    setPromptDraft(payload.question.prompt || "");
    setCorrectAnswerDraft(payload.question.correctAnswer || "");
    setEditing(true);
    setError("");
  };

  const submitEdit = async () => {
    setBusy("edit");
    setError("");
    try {
      const result = await editQuestion(uploadId, sourceKey, {
        baseRevisionId: review?.currentRevisionId ?? null,
        prompt: promptDraft,
        correctAnswer: correctAnswerDraft,
      });
      if (result.questionPayload) onPayload(result.questionPayload);
      setEditing(false);
      await refreshReviewAndRevisions();
    } catch (requestError) {
      // 乐观并发冲突或质量门禁拒绝都会走到这里；不静默丢弃错误，同时把面板刷新到
      // 服务端当前真实版本，方便老师基于最新内容重试，而不是对着一份已经过期的题目改。
      setError(requestError instanceof Error ? requestError.message : "题目编辑失败");
      await refreshReviewAndRevisions().catch(() => undefined);
    } finally {
      setBusy("");
    }
  };

  const activate = async (revisionId: string) => {
    setBusy(`activate:${revisionId}`);
    setError("");
    try {
      const result = await activateQuestionRevision(uploadId, sourceKey, revisionId);
      if (result.questionPayload) onPayload(result.questionPayload);
      await refreshReviewAndRevisions();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "回滚历史版本失败");
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
        {!editing && <button type="button" disabled={Boolean(busy)} onClick={beginEdit}>人工编辑</button>}
      </div>

      {editing && (
        <div className="question-review-edit">
          <label>
            题干
            <textarea
              value={promptDraft}
              onChange={(event) => setPromptDraft(event.target.value)}
              rows={4}
            />
          </label>
          <label>
            标准答案
            <input value={correctAnswerDraft} onChange={(event) => setCorrectAnswerDraft(event.target.value)} />
          </label>
          <div className="question-review-edit-actions">
            <button type="button" disabled={busy === "edit"} onClick={() => void submitEdit()}>
              {busy === "edit" ? "保存中…" : "保存编辑"}
            </button>
            <button type="button" className="ghost" disabled={busy === "edit"} onClick={() => setEditing(false)}>取消</button>
          </div>
        </div>
      )}

      {revisions.length > 0 && (
        <ul className="question-review-history">
          {revisions.slice().reverse().map((revision) => {
            const isCurrent = revision.revisionId === review?.currentRevisionId;
            const activating = busy === `activate:${revision.revisionId}`;
            return (
              <li key={revision.revisionId} className={isCurrent ? "current" : undefined}>
                <span>
                  第 {revision.revisionNumber} 版 · {REVISION_SOURCE_LABEL[revision.revisionSource || "model_generated"]}
                  {isCurrent ? " · 当前版本" : ""}
                </span>
                {!isCurrent && (
                  <button type="button" disabled={Boolean(busy)} onClick={() => void activate(revision.revisionId)}>
                    {activating ? "回滚中…" : "设为当前版本"}
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
}
