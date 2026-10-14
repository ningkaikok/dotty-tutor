import { useCallback, useEffect, useState } from "react";
import { Badge, Button, Notice } from "../../components/ui";
import { loadSourceCaseAiOpinion, requestSourceCaseAiOpinion, type SourceCaseAiOpinion as Opinion } from "../../api/aiReview";
import { issueLabel, verdictLabel } from "../courseware/aiReviewView";

const verdictTone = { pass: "success", concern: "warning", fail: "danger" } as const;

/** Advisory AI second opinion on one source case; it never records or replaces the human decision. */
export function SourceCaseAiOpinion({ caseId, recordVersion }: { caseId: string; recordVersion: number }) {
  const [opinion, setOpinion] = useState<Opinion | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async (signal?: AbortSignal) => {
    try {
      const next = await loadSourceCaseAiOpinion(caseId, signal);
      if (!signal?.aborted) { setOpinion(next); setError(""); }
    } catch (requestError) {
      if (!signal?.aborted) setError(requestError instanceof Error ? requestError.message : "AI 参考意见暂时无法读取。");
    }
  }, [caseId]);

  useEffect(() => {
    const controller = new AbortController();
    void reload(controller.signal);
    return () => controller.abort();
  }, [reload, recordVersion]);

  useEffect(() => {
    if (!opinion?.jobId) return;
    const timer = window.setTimeout(() => void reload(), 2000);
    return () => window.clearTimeout(timer);
  }, [opinion, reload]);

  const request = async () => {
    setBusy(true);
    setError("");
    try { setOpinion(await requestSourceCaseAiOpinion(caseId, recordVersion)); } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "AI 参考意见未能启动。");
    } finally { setBusy(false); }
  };

  const current = opinion?.verdict && !opinion.stale ? opinion : null;
  return <section className="panel quality-review-ai-opinion" aria-label="AI 参考意见">
    <div className="quality-review-section-heading"><h3>AI 参考意见</h3><Badge tone="info">不是人工决定，不计入金标准</Badge></div>
    {error && <Notice tone="danger">{error}</Notice>}
    {opinion?.jobId ? <Notice tone="info">审核模型正在对照原页，完成后自动显示。</Notice>
      : !current && <Button type="button" disabled={busy || !opinion} onClick={() => void request()}>
        {opinion?.stale ? "案例已修订，重新请求 AI 参考意见（调用 1 次审核模型）" : "请求 AI 参考意见（调用 1 次审核模型）"}
      </Button>}
    {current?.verdict && <>
      <p><Badge tone={verdictTone[current.verdict]}>{verdictLabel[current.verdict]}</Badge>{current.textOnly && <Badge>仅对照文字</Badge>} {current.summary}</p>
      {Boolean(current.issues?.length) && <ul>{current.issues?.map((issue, index) => <li key={`${issue.code}-${index}`}>
        {issueLabel[issue.code]}{issue.sourcePage ? ` · 原页 ${issue.sourcePage}` : ""}：{issue.message}{issue.quote && <q>{issue.quote}</q>}
      </li>)}</ul>}
      <p>请对照原页自行判断；下方的最终决定仍由你提交。</p>
    </>}
  </section>;
}
