import { useState } from "react";
import type { ChapterAttemptResult } from "../../types/chapter";

interface ChapterAttemptReviewProps {
  busy: boolean;
  error: string;
  result: ChapterAttemptResult | null;
  onLoad: (attemptId: string) => Promise<unknown>;
  onReview: (attemptId: string, decision: "correct" | "incorrect", note: string) => Promise<unknown>;
}

export function ChapterAttemptReview({ busy, error, result, onLoad, onReview }: ChapterAttemptReviewProps) {
  const [attemptId, setAttemptId] = useState("");
  const [note, setNote] = useState("");
  return (
    <section className="chapter-panel chapter-attempt-review" aria-label="学生简答教师复核">
      <span className="eyebrow">英语简答与存疑作答</span>
      <h2>教师复核</h2>
      <p>学生答案或原文证据未达到确定性规则时会留在 needs_review。输入学生页面显示的作答编号，人工核对答案与依据后记录判定。</p>
      <form onSubmit={(event) => { event.preventDefault(); void onLoad(attemptId.trim()); }}>
        <label>作答编号<input value={attemptId} onChange={(event) => setAttemptId(event.target.value)} required /></label>
        <button type="submit" disabled={busy}>{busy ? "读取中…" : "读取作答"}</button>
      </form>
      {error && <p role="alert" className="chapter-error">{error}</p>}
      {result && <div className="chapter-attempt-review-detail">
        <p><strong>学生答案：</strong>{String(result.answer?.text ?? result.answer?.numericAnswer ?? "（空）")}</p>
        <p><strong>系统判定：</strong>{result.assessment} · 依据 {result.evidenceVerdict}</p>
        <ul>{(result.evidenceRefs ?? []).map((ref, index) => <li key={`${ref.sourceRevisionId}-${ref.page}-${index}`}>第 {ref.page} 页{ref.quote ? `：“${ref.quote}”` : ""}</li>)}</ul>
        {result.reviews?.map((review, index) => <p key={`${review.reviewer}-${index}`}>教师复核：{review.decision} · {review.note}</p>)}
        {result.assessment === "needs_review" && <form onSubmit={(event) => {
          event.preventDefault();
          void onReview(attemptId.trim(), "correct", note.trim());
        }}>
          <label>教师判断说明<textarea rows={3} value={note} onChange={(event) => setNote(event.target.value)} required /></label>
          <div className="chapter-review-actions">
            <button type="button" disabled={busy} onClick={() => void onReview(attemptId.trim(), "incorrect", note.trim())}>判为需要修正</button>
            <button type="submit" className="primary" disabled={busy || !note.trim()}>确认答案与依据正确</button>
          </div>
        </form>}
      </div>}
    </section>
  );
}
