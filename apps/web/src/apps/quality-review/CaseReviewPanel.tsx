import { useState } from "react";
import { Button, Notice, SelectField, Skeleton, TextAreaField, TextField } from "../../components/ui";
import { apiResourceUrl } from "../../api/client";
import type { QualityReviewCase, QualityReviewInboxItem } from "../../api/qualityReviews";
import { useQualityReview } from "./useQualityReview";
import "./qualityReview.css";

const decisionLabels = { accepted: "接受", rejected: "拒绝", needs_changes: "要求补充" } as const;

export type CaseReviewPanelProps = {
  packageId?: string;
  caseId?: string;
  chapterId?: string;
  uploadId?: string;
  onCaseSelected?: (item: QualityReviewInboxItem) => void;
  onReturn?: () => void;
  returnLabel?: string;
  showReturn?: boolean;
};

/** Reusable source-case list and immutable review detail for the library and chapter workspace. */
export function CaseReviewPanel({
  packageId, caseId, chapterId, uploadId, onCaseSelected, onReturn, returnLabel = "返回我的教材", showReturn = false,
}: CaseReviewPanelProps) {
  const review = useQualityReview({ packageId, caseId, chapterId, uploadId, onCaseSelected });
  const [sourceFilter, setSourceFilter] = useState("");
  const [chapterFilter, setChapterFilter] = useState("");
  const packageSource = review.detail?.source ?? {};
  const sourceLocator = review.detail?.sourceLocator ?? {};
  const visibleCases = review.cases.filter((item) => (!sourceFilter || item.sourceKey === sourceFilter)
    && (!chapterFilter || item.chapterIds.includes(chapterFilter)));
  const sourceOptions = [...new Map(review.cases.map((item) => [item.sourceKey, item.sourceTitle])).entries()];
  const chapterOptions = [...new Set(review.cases.flatMap((item) => item.chapterIds))];

  return <section className={`quality-review-panel${chapterId ? " quality-review-embedded" : ""}`} aria-label="教材来源复核">
    <header className="quality-review-panel-heading">
      <div><span className="eyebrow">原文、证据与人工决定</span><h2>{chapterId ? "教材来源复核" : "来源复核详情"}</h2>
        <p>来源案例决定只记录教材证据，不会自动批准课程内容或教学视频；机检和匿名评分仍需人工核对。</p>
      </div>
      <div className="quality-review-panel-actions">
        {review.hasCounts && <span>{review.counts.pendingCount} 项待复核 · {review.counts.reviewedCount} 项已复核</span>}
        <Button disabled={review.busy || review.transitioning || review.inboxLoading} onClick={() => void review.refresh()}>刷新来源案例</Button>
        {showReturn && <Button onClick={onReturn}>{returnLabel}</Button>}
      </div>
    </header>

    {review.inboxError && <Notice tone="danger" className="quality-review-alert">来源案例列表无法读取：{review.inboxError}</Notice>}
    {review.error && <Notice tone="danger" className="quality-review-alert">{review.error}{review.selectedCaseId && <Button variant="quiet" disabled={review.transitioning || review.busy} onClick={() => void review.openCase(review.selectedCaseId)}>重试打开案例</Button>}</Notice>}
    {review.notice && <Notice tone="info" className="quality-review-notice">{review.notice}</Notice>}
    {review.requestedCaseMissing && <Notice tone="danger" className="quality-review-alert">当前复核上下文中找不到这个案例；请返回“我的教材”重新打开，或调整教材与章节筛选。</Notice>}

    <div className="quality-review-layout">
      <aside className="quality-review-sidebar" aria-label="来源复核案例">
        {!chapterId && <div className="quality-review-list-filters">
          <SelectField label="按教材或来源筛选" value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}>
            <option value="">全部来源</option>{sourceOptions.map(([key, title]) => <option key={key} value={key}>{title}</option>)}
          </SelectField>
          <SelectField label="按章节筛选" value={chapterFilter} onChange={(event) => setChapterFilter(event.target.value)}>
            <option value="">全部章节</option>{chapterOptions.map((id) => <option key={id} value={id}>{id}</option>)}
          </SelectField>
        </div>}
        <section className="panel">
          <h3>{chapterId ? "本章来源案例" : "来源案例"}</h3>
          {review.inboxLoading && <Skeleton label="正在读取来源案例…" rows={2} />}
          {!review.inboxLoading && !review.inboxError && visibleCases.length === 0 && <p>{chapterId ? "当前来源尚未关联复核案例。" : "没有符合筛选条件的来源案例。"}</p>}
          <ul className="quality-review-case-list">{visibleCases.map((item) => <li key={item.caseId}>
            <button type="button" aria-current={item.caseId === review.selectedCaseId ? "true" : undefined} aria-label={`复核来源案例 ${item.externalCaseId}`} onClick={() => void review.openCase(item.caseId)}>
              <strong>{item.externalCaseId}</strong><span>{item.subject} · PDF 第 {item.sourcePage} 页</span><small>{caseStatusLabel(item.reviewStatus, item.sourceVerification)}</small>
            </button>
          </li>)}</ul>
        </section>
      </aside>

      <div className="quality-review-detail">
        {!review.detail ? <section className="panel"><h3>{review.transitioning ? "正在读取所选案例…" : "选择一个来源案例"}</h3><p>复核记录会与来源、输入和输出指纹关联。</p></section> : <>
          <section className="panel quality-review-source">
            <div className="quality-review-section-heading"><div><span className="eyebrow">{review.detail.subject === "math" ? "数学来源" : review.detail.subject === "english" ? "英语来源" : review.detail.subject}</span><h3>{review.detail.externalCaseId}</h3></div><span className="quality-review-status">{caseStatusLabel(review.detail.reviewStatus, review.detail.sourceVerification)}</span></div>
            <div className="quality-review-provenance"><div><b>来源</b><span>{String(packageSource.sourceTitle ?? packageSource.title ?? "教材来源")}</span></div><div><b>PDF 页码</b><span>{review.detail.sourcePage}</span></div><div><b>版本</b><span>{String(packageSource.version ?? "未标明")}</span></div><div><b>许可</b><span>{String(packageSource.license ?? "待核实")}</span></div><div><b>服务端 SHA-256</b><code>{String(packageSource.contentSha256 ?? "尚未核验")}</code></div></div>
            {typeof packageSource.url === "string" && <p><a href={packageSource.url} target="_blank" rel="noreferrer">打开来源网页</a></p>}
            <h4>原文定位</h4><blockquote>{String(sourceLocator.quote ?? "来源引用缺失")}</blockquote>
            {packageSource.assetVerification === "server_hash_verified" ? <figure className="quality-review-source-page"><img src={apiResourceUrl(`/api/quality-reviews/cases/${encodeURIComponent(review.detail.caseId)}/source-page`)} alt={`教材 PDF 第 ${review.detail.sourcePage} 页`} /><figcaption>受管原页、PDF 指纹、页码和引用已由服务端核验；案例仍需人工复核。</figcaption></figure> : <div className="quality-review-source-gate" role="status"><strong>原教材 PDF 尚未连接</strong><p>请通过现有教材上传流程登记原 PDF。未能从受管原件核验的决定只会保存为临时意见。</p></div>}
            <div className="quality-review-source-gate" role="note"><strong>人工决定仍由你提交</strong><p>预期答案、OCR 和机检结果不代表教材正确。公开教材案例不计入人工金标准，未验证身份不表示独立双审。</p></div>
            <div className="quality-review-fingerprints"><span>来源 {review.detail.sourceFingerprint}</span><span>输入 {review.detail.inputFingerprint}</span><span>输出 {review.detail.outputFingerprint}</span></div>
          </section>

          <section className="panel quality-review-input"><h3>案例输入与真实 OCR 记录</h3><ReviewSummary value={review.detail.input} kind="input" /></section>
          <section className="quality-review-comparison">
            <article className="panel"><h3>预期结果</h3><ReviewSummary value={review.detail.expected} kind="expected" /></article>
            <article className="panel"><h3>实际观察</h3><ReviewSummary value={review.detail.actual} kind="actual" /></article>
          </section>

          <section className="panel quality-review-revisions">
            <h3>版本历史</h3><p>每次修订都会追加新版本；旧版决定仍绑定原有指纹。</p>
            <details><summary>查看 {review.detail.revisions.length} 个不可变版本</summary>
              {review.detail.revisions.map((revision) => <article className="quality-review-revision" key={revision.recordVersion}>
                <strong>版本 {revision.recordVersion}</strong><span>作者 {revision.createdBy} · {new Date(revision.createdAt * 1000).toLocaleString()}</span>
                <div>输入 {revision.inputFingerprint} · 预期 {revision.expected && JSON.stringify(revision.expected)} · 实际 {revision.outputFingerprint}</div>
              </article>)}
            </details>
            <QualityReviewQuickRevision key={`${review.detail.caseId}:${review.detail.recordVersion}`} detail={review.detail} onSubmit={review.reviseCase} busy={review.busy || review.transitioning} />
            <QualityReviewRevisionEditor key={`advanced-${review.detail.caseId}:${review.detail.recordVersion}`} detail={review.detail} onSubmit={review.reviseCase} busy={review.busy || review.transitioning} />
          </section>

          <section className="panel quality-review-decision">
            <h3>{packageSource.assetVerification === "server_hash_verified" ? "提交你的最终单人教材决定" : "记录待核验意见"}</h3><p>当前身份未验证为独立复核者。显示姓名仅用于记录；不同姓名不会被视为不同审阅者。</p>
            {review.detail.decisions.map((item) => <article className="quality-review-prior-decision" key={item.reviewId}><strong>{item.displayName || item.actorId} · {item.stage === "provisional" ? "临时" : "最终"}{decisionLabels[item.decision]}</strong><span>版本 {item.reviewedRecordVersion} · {item.stage === "provisional" ? "原页未核验" : "原页已核验"} · {new Date(item.createdAt * 1000).toLocaleString()}</span>{item.claimedQuote && <blockquote>用户自报摘录（未由服务端核验）：{item.claimedQuote}</blockquote>}{item.verifiedQuote && <blockquote>服务端核对摘录：{item.verifiedQuote}</blockquote>}<p>{item.rationale}</p></article>)}
            <div className="quality-review-decision-form">
              <TextField label="显示姓名" value={review.displayName} maxLength={128} onChange={(event) => review.setDisplayName(event.target.value)} placeholder="仅作显示，身份未验证" />
              <SelectField label="意见" value={review.decision} onChange={(event) => review.setDecision(event.target.value as "accepted" | "rejected" | "needs_changes")}><option value="accepted">初步认可</option><option value="rejected">初步拒绝</option><option value="needs_changes">待补充依据</option></SelectField>
              <TextAreaField label="复核理由" rows={4} value={review.rationale} onChange={(event) => review.setRationale(event.target.value)} placeholder="说明你查看了哪些来源证据以及仍需核对的内容" />
              <TextAreaField label={packageSource.assetVerification === "server_hash_verified" ? "原文核对摘录（系统会与受管教材原页比对）" : "用户自报原文摘录（未接入教材原页，不会标为已核验）"} rows={2} value={review.verifiedQuote} onChange={(event) => review.setVerifiedQuote(event.target.value)} placeholder="复制原页中与你的决定相关的准确文字" />
              <Button variant="primary" className="quality-review-primary" busy={review.busy} disabled={!review.rationale.trim() || review.transitioning} onClick={() => void review.saveDecision()}>{review.busy ? "保存中…" : (review.verifiedQuote.trim() && packageSource.assetVerification === "server_hash_verified") || review.detail.sourceVerification === "human_quote_verified" ? "提交最终单人决定" : "保存待核验意见"}</Button>
            </div>
          </section>
        </>}
      </div>
    </div>
  </section>;
}

function ReviewSummary({ value, kind }: { value: Record<string, unknown>; kind: "input" | "expected" | "actual" }) {
  const questionNumbers = stringList(value.questionNumbers);
  const acceptedAnswers = stringList(value.acceptedAnswers);
  const requiredFragments = stringList(value.requiredSourceFragments);
  const evidence = typeof value.evidenceQuote === "string" ? value.evidenceQuote : "";
  const checks = isRecord(value.checks) ? value.checks : {};
  const failedChecks = Object.entries(checks).filter(([, passed]) => passed === false).map(([name]) => name);
  const production = isRecord(value.productionEvaluation) ? value.productionEvaluation : {};
  const resultText = typeof production.resultText === "string" ? production.resultText : "";
  const teacherObservation = typeof value.teacherObservation === "string" ? value.teacherObservation : "";
  const limitations = typeof value.limitations === "string" ? value.limitations : "";
  const question = firstText(value, ["question", "prompt", "questionText"]);
  const passage = firstText(value, ["passage", "ocrText"]);
  const providedAnswer = firstText(value, ["providedAnswer"]);
  const ocrObservation = isRecord(value.realOcrObservation) ? value.realOcrObservation : null;
  const ocrMetadata = isRecord(ocrObservation?.metadata) ? ocrObservation.metadata : {};
  const ocrRuntime = isRecord(ocrMetadata.runtime) ? ocrMetadata.runtime : {};
  const recognizedText = ocrObservation && typeof ocrObservation.recognizedText === "string" ? ocrObservation.recognizedText : "";
  const method = typeof value.method === "string" ? value.method : "";
  const operationLabel = kind === "actual" && value.textSource === "pypdf_text_layer"
    ? "本栏是 PDF 文字层题号回放；独立 OCR 执行记录列在案例输入中。"
    : method === "domain.chapters.english.evaluate_english_chapter_attempt"
      ? "执行方式：英语答案与证据规则回放"
      : method ? `执行方式：${method}` : "";

  return <>
    {kind === "input" && question && <p className="quality-review-question">{question}</p>}
    {kind === "input" && passage && <blockquote className="quality-review-passage">{passage.length > 1800 ? `${passage.slice(0, 1800)}…` : passage}</blockquote>}
    {kind === "input" && providedAnswer && <p><strong>输入答案：</strong>{providedAnswer}</p>}
    {ocrObservation && <section className="quality-review-ocr-evidence" aria-label="真实 OCR 执行记录">
      <h4>真实 OCR 执行记录（待人工核对）</h4>
      <p>来源类型：{String(ocrMetadata.sourceKind ?? "未知")} · 服务商：{String(ocrRuntime.provider ?? "未知")} · 用时：{typeof ocrMetadata.elapsedMs === "number" ? `${(ocrMetadata.elapsedMs / 1000).toFixed(1)} 秒` : "未知"} · 成本：{ocrMetadata.cost == null ? "未提供" : String(ocrMetadata.cost)}</p>
      {typeof ocrObservation.engineeringFinding === "string" && <p className="quality-review-failed-check">{ocrObservation.engineeringFinding}</p>}
      {typeof ocrObservation.expectedVisualCheck === "string" && <p>预期原页核验点：{ocrObservation.expectedVisualCheck}</p>}
      {recognizedText && <details><summary>查看实际识别原文（{recognizedText.length} 字符）</summary><pre>{recognizedText}</pre></details>}
    </section>}
    <ul className="quality-review-summary-list">
      {operationLabel && <li>{operationLabel}</li>}
      {questionNumbers.length > 0 && <li>{kind === "expected" ? "应识别题号" : "识别到的题号"}：{questionNumbers.join("、")}</li>}
      {acceptedAnswers.length > 0 && <li>{kind === "expected" ? "预期答案" : "实际答案"}：{acceptedAnswers.join("；")}</li>}
      {requiredFragments.length > 0 && <li>{kind === "expected" ? "应保留的原文" : "检测到的原文片段"}：{requiredFragments.join("；")}</li>}
      {evidence && <li>{kind === "expected" ? "预期证据" : "实际证据"}：{evidence}</li>}
      {resultText && <li>实际响应：{resultText}</li>}
      {teacherObservation && <li><strong>人工补充观察：</strong>{teacherObservation}</li>}
      {isRecord(value.productionEvaluation) && typeof value.productionEvaluation.assessment === "string" && <li>评测结果：{value.productionEvaluation.assessment}{typeof value.productionEvaluation.evidenceVerdict === "string" ? ` · 证据 ${value.productionEvaluation.evidenceVerdict}` : ""}</li>}
      {failedChecks.length > 0 && <li className="quality-review-failed-check">机检失败项：{failedChecks.join("、")}</li>}
      {!questionNumbers.length && !acceptedAnswers.length && !requiredFragments.length && !evidence && !resultText && !question && !passage && !providedAnswer && !ocrObservation && !teacherObservation && <li>{kind === "input" ? "未提供题目或段落输入。" : kind === "actual" ? "未提供可直接比较的结果字段。" : "未提供结构化预期结果。"}</li>}
    </ul>
    {limitations && <p className="quality-review-limitations">机检局限：{limitations}</p>}
    <details className="quality-review-raw-data"><summary>查看完整记录</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>
  </>;
}

function QualityReviewQuickRevision({ detail, onSubmit, busy }: {
  detail: QualityReviewCase;
  onSubmit: (snapshot: { input: Record<string, unknown>; expected: Record<string, unknown>; actual: Record<string, unknown> }) => Promise<boolean>;
  busy: boolean;
}) {
  const [question, setQuestion] = useState(() => firstText(detail.input, ["question", "prompt", "questionText"]));
  const [passage, setPassage] = useState(() => firstText(detail.input, ["passage", "ocrText"]));
  const [answers, setAnswers] = useState(() => stringList(detail.expected.acceptedAnswers).join("\n"));
  const [evidence, setEvidence] = useState(() => firstText(detail.expected, ["evidenceQuote"]));
  const [observation, setObservation] = useState(() => firstText(detail.actual, ["teacherObservation"]));
  const submit = () => {
    const input = { ...detail.input, question, passage };
    const expected = { ...detail.expected, acceptedAnswers: answers.split(/\n|；|;/).map((item) => item.trim()).filter(Boolean), evidenceQuote: evidence };
    const actual = { ...detail.actual, teacherObservation: observation };
    void onSubmit({ input, expected, actual });
  };
  return <section className="quality-review-quick-revision">
    <h4>修订案例要点</h4><p>用普通字段追加新快照；系统保留其他来源与 OCR 记录，不覆盖旧版决定。</p>
    <TextAreaField label="问题或题目" rows={2} value={question} onChange={(event) => setQuestion(event.target.value)} maxLength={4000} />
    <TextAreaField label="原文或输入段落" rows={3} value={passage} onChange={(event) => setPassage(event.target.value)} maxLength={12000} />
    <TextAreaField label="预期答案（每行一项）" rows={3} value={answers} onChange={(event) => setAnswers(event.target.value)} maxLength={4000} />
    <TextAreaField label="预期原文依据" rows={2} value={evidence} onChange={(event) => setEvidence(event.target.value)} maxLength={4000} />
    <TextAreaField label="实际观察补充" rows={2} value={observation} onChange={(event) => setObservation(event.target.value)} maxLength={4000} />
    <Button disabled={busy} onClick={submit}>保存内容修订</Button>
  </section>;
}

function QualityReviewRevisionEditor({ detail, onSubmit, busy }: {
  detail: QualityReviewCase;
  onSubmit: (snapshot: { input: Record<string, unknown>; expected: Record<string, unknown>; actual: Record<string, unknown> }) => Promise<boolean>;
  busy: boolean;
}) {
  const [snapshot, setSnapshot] = useState(() => ({
    input: JSON.stringify(detail.input, null, 2),
    expected: JSON.stringify(detail.expected, null, 2),
    actual: JSON.stringify(detail.actual, null, 2),
  }));
  const [error, setError] = useState("");
  const submit = async () => {
    try {
      const values = {
        input: JSON.parse(snapshot.input) as unknown,
        expected: JSON.parse(snapshot.expected) as unknown,
        actual: JSON.parse(snapshot.actual) as unknown,
      };
      if (![values.input, values.expected, values.actual].every(isRecord)) throw new Error("每个修订字段都必须是 JSON 对象");
      if (await onSubmit(values as { input: Record<string, unknown>; expected: Record<string, unknown>; actual: Record<string, unknown> })) setError("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "修订 JSON 无效"); }
  };
  return <details className="quality-review-revision-editor"><summary>高级：结构化快照修订</summary>
    <p>仅在需要调整非标准字段时使用；编辑会产生新版本并保留原始决定。</p>
    {(["input", "expected", "actual"] as const).map((key) => <TextAreaField key={key} label={key === "input" ? "案例输入" : key === "expected" ? "预期结果" : "实际观察"} rows={7} value={snapshot[key]} onChange={(event) => setSnapshot((current) => ({ ...current, [key]: event.target.value }))} />)}
    {error && <Notice tone="danger" className="quality-review-alert">{error}</Notice>}
    <Button variant="primary" className="quality-review-primary" disabled={busy} onClick={() => void submit()}>追加结构化版本</Button>
  </details>;
}

function caseStatusLabel(status: string, sourceVerification: string): string {
  if (status === "pending_source_verification" && sourceVerification === "managed_asset_verified") return "原页已校验 · 待人工复核";
  if (status === "decision_recorded_pending_source") return "有临时意见 · 待原页核验";
  if (status === "pending_source_verification") return "原 PDF 待受管校验";
  if (status === "pending_human_review") return sourceVerification === "managed_asset_verified" ? "原页已校验 · 待人工复核" : "待人工复核";
  if (status === "human_accepted") return "人工已接受 · 原页已核验";
  if (status === "human_rejected") return "人工已拒绝 · 原页已核验";
  if (status === "needs_changes") return "人工要求补充";
  return status;
}

function firstText(value: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) if (typeof value[key] === "string" && value[key]) return value[key] as string;
  return "";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.length > 0) : [];
}
