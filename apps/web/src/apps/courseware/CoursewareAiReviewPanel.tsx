import { Badge, Button, Card, Notice, Skeleton } from "../../components/ui";
import type { AiPageReview, CoursewareAiReviewStatus } from "../../api/aiReview";
import { aiReviewGroups, issueLabel, metricsSummary, samplingLabel, verdictLabel } from "./aiReviewView";

const verdictTone = { pass: "success", concern: "warning", fail: "danger" } as const;

function FlaggedPage({ page, onOpen }: { page: AiPageReview; onOpen?: (pageId: string) => void }) {
  return <li className="courseware-ai-page">
    <div className="ui-actions">
      <strong>{page.title}</strong>
      {page.verdict && <Badge tone={verdictTone[page.verdict]}>{verdictLabel[page.verdict]}</Badge>}
      {page.verdict === "pass" && page.sampled && <Badge tone="info">抽检</Badge>}
      {page.textOnly && <Badge>仅对照文字</Badge>}
      {onOpen && <Button type="button" onClick={() => onOpen(page.pageId)}>打开此页</Button>}
    </div>
    {page.summary && <p>{page.summary}</p>}
    {Boolean(page.issues?.length) && <ul>{page.issues?.map((issue, index) => <li key={`${issue.code}-${index}`}>
      <span>{issue.severity === "blocking" ? "阻断" : "提示"} · {issueLabel[issue.code]}{issue.sourcePage ? ` · 原页 ${issue.sourcePage}` : ""}：{issue.message}</span>
      {issue.quote && <q>{issue.quote}</q>}
    </li>)}</ul>}
  </li>;
}

/** Advisory AI pre-review: routes pages to the teacher; confirming is still a teacher review action. */
export function CoursewareAiReviewPanel({ status, error, starting, busy, onStart, onBulkConfirm, onOpenPage, onReload }: {
  status: CoursewareAiReviewStatus | null;
  error: string;
  starting: boolean;
  busy: boolean;
  onStart: () => void;
  onBulkConfirm: (pageIds: string[]) => void;
  onOpenPage?: (pageId: string) => void;
  onReload?: () => void;
}) {
  if (!status) return <Card aria-label="AI 初审">{error ? <Notice tone="danger">{error}</Notice> : <Skeleton label="正在读取当前版本的 AI 初审结果…" rows={1} />}{onReload && <Button type="button" disabled={busy || starting} onClick={onReload}>重新读取初审结果</Button>}</Card>;
  const groups = aiReviewGroups(status);
  const running = Boolean(status.jobId);
  return <Card className="courseware-ai-review" aria-label="AI 初审">
    <div className="ui-actions"><h2>AI 初审</h2><Badge tone="info">仅供参考，由教师确认</Badge></div>
    <p>审核模型逐页对照原页检查摘录、公式、图文和答案。AI 不会批准任何页面，也不计入人工金标准；判为通过的页面按 {samplingLabel(status.samplingRate)} 抽检给教师细看。</p>
    {error && <Notice tone="danger">{error}</Notice>}
    {running ? <Notice tone="info">AI 初审进行中，完成后自动刷新结果。</Notice>
      : <Button type="button" disabled={busy || starting || status.pagesToReview === 0} onClick={onStart}>
        {status.pagesToReview === 0 ? "待审页面均已完成 AI 初审" : starting ? "正在提交…" : `AI 初审 ${status.pagesToReview} 页（将调用 ${status.pagesToReview} 次审核模型）`}
      </Button>}
    <p role="status">{metricsSummary(status.metrics)}</p>
    <p>统计仅包含问题页和抽检页中，AI 初审后教师对同一页面版本的复核；批量确认不计入独立复核样本。样本存在选择偏差，不代表全部页面的漏报率。</p>

    <section aria-labelledby="ai-review-needs">
      <h3 id="ai-review-needs">需要教师细看（{groups.needsReview.length}）</h3>
      {groups.needsReview.length ? <ul>{groups.needsReview.map((page) => <FlaggedPage key={page.pageId} page={page} onOpen={onOpenPage} />)}</ul> : <p>没有被标记或抽中的页面。</p>}
    </section>

    <section aria-labelledby="ai-review-bulk">
      <h3 id="ai-review-bulk">AI 未发现问题、未被抽检（{groups.bulkConfirmable.length}）</h3>
      {groups.bulkConfirmable.length > 0 && <>
        <p>{groups.bulkConfirmable.map((page) => page.title).join("、")}</p>
        <Button type="button" disabled={busy || running} onClick={() => onBulkConfirm(groups.bulkConfirmable.map((page) => page.pageId))}>
          教师批量确认这 {groups.bulkConfirmable.length} 页
        </Button>
      </>}
    </section>

    {groups.unreviewed.length > 0 && <p>尚未初审或原结论已过期，需重新初审：{groups.unreviewed.map((page) => page.title).join("、")}</p>}
  </Card>;
}
