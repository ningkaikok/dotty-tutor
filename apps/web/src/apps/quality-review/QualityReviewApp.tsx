import { useState } from "react";
import { useNavigate } from "react-router";
import { apiResourceUrl } from "../../api/client";
import { MaterialsHeader } from "../materials/MaterialsHeader";
import { QualityReviewRunsPanel } from "./QualityReviewRunsPanel";
import { useQualityReview } from "./useQualityReview";
import "./qualityReview.css";

const decisionLabels = { accepted: "接受", rejected: "拒绝", needs_changes: "要求补充" } as const;

export function QualityReviewApp() {
  const navigate = useNavigate();
  const [manifest, setManifest] = useState<unknown>(null);
  const {
    packages, selectedPackageId, cases, selectedCaseId, detail,
    displayName, setDisplayName, decision, setDecision, rationale, setRationale,
    verifiedQuote, setVerifiedQuote, busy, loading, transitioning, error, setError, notice,
    refreshPackages, selectPackage, openCase, importPacket, saveDecision, reviseCase,
  } = useQualityReview();

  const importManifest = async () => {
    if (manifest && await importPacket(manifest)) setManifest(null);
  };

  const packageSource = detail?.source ?? {};
  const sourceLocator = detail?.sourceLocator ?? {};

  return <main className="quality-review-shell">
    <MaterialsHeader backTo="/studio" backLabel="教材与课程" subtitle="质量复核" actions={<button type="button" disabled={busy || transitioning} onClick={() => void refreshPackages()}>刷新</button>} />
    <section className="quality-review-heading">
      <span className="eyebrow">来源、版本与用户决定</span>
      <h1>质量复核工作台</h1>
      <p>查看来源定位、预期和实际输出，并把个人决定保存到数据库。公开教材和合成案例永远不计入人工金标准。</p>
    </section>

    <section className="quality-review-import panel" aria-label="导入复核包">
      <div><h2>导入来源复核包</h2><p>选择准备好的来源复核 JSON。系统会按受管素材编号核验教材原 PDF，不接受文件中的服务器路径。</p></div>
      <label>来源复核 JSON<input type="file" accept="application/json,.json" disabled={busy} onChange={(event) => {
        const file = event.currentTarget.files?.[0];
        if (!file) return;
        void file.text().then((text) => {
          try { setManifest(JSON.parse(text) as unknown); setError(""); }
          catch { setError("JSON 文件无法解析"); }
        });
      }} /></label>
      <button type="button" className="quality-review-primary" disabled={!manifest || busy} onClick={() => void importManifest()}>{busy ? "正在导入…" : "导入待复核案例"}</button>
    </section>

    {error && <p className="quality-review-alert" role="alert">{error}</p>}
    {notice && <p className="quality-review-notice" role="status">{notice}</p>}
    {loading ? <p role="status">正在读取复核记录…</p> : <div className="quality-review-layout">
      <aside className="quality-review-sidebar">
        <section className="panel">
          <h2>来源包</h2>
          {!packages.length ? <p>还没有复核包。导入准备结果后会在这里显示。</p> : <ul className="quality-review-package-list">{packages.map((item) => <li key={item.packageId}>
            <button type="button" aria-current={item.packageId === selectedPackageId ? "true" : undefined} onClick={() => void selectPackage(item.packageId)}><strong>{item.title}</strong><span>{item.cases ?? 0} 案例 · {item.pending ?? 0} 项待人工复核</span></button>
          </li>)}</ul>}
        </section>
        {selectedPackageId && <section className="panel">
          <h2>案例</h2>
          <ul className="quality-review-case-list">{cases.map((item) => <li key={item.caseId}><button type="button" aria-current={item.caseId === selectedCaseId ? "true" : undefined} onClick={() => void openCase(item.caseId)}>
            <strong>{item.externalCaseId}</strong><span>{item.subject} · PDF 第 {item.sourcePage} 页</span><small>{caseStatusLabel(item.reviewStatus, item.sourceVerification)}</small>
          </button></li>)}</ul>
        </section>}
      </aside>

      <section className="quality-review-detail">
        {!detail ? <section className="panel"><h2>{transitioning ? "正在读取所选案例…" : "选择一个案例"}</h2><p>复核记录保存后会与其来源和输出指纹关联。</p></section> : <>
          <section className="panel quality-review-source">
            <div className="quality-review-section-heading"><div><span className="eyebrow">{detail.subject === "math" ? "数学来源" : detail.subject === "english" ? "英语来源" : detail.subject}</span><h2>{detail.externalCaseId}</h2></div><span className="quality-review-status">{caseStatusLabel(detail.reviewStatus, detail.sourceVerification)}</span></div>
            <div className="quality-review-provenance"><div><b>来源</b><span>{String(packageSource.sourceTitle ?? packageSource.title ?? "OpenStax")}</span></div><div><b>PDF 页码</b><span>{detail.sourcePage}</span></div><div><b>版本</b><span>{String(packageSource.version ?? "未标明")}</span></div><div><b>许可</b><span>{String(packageSource.license ?? "待核实")}</span></div><div><b>服务端 SHA-256</b><code>{String(packageSource.contentSha256 ?? "尚未核验")}</code></div></div>
            {typeof packageSource.url === "string" && <p><a href={packageSource.url} target="_blank" rel="noreferrer">打开来源网页</a></p>}
            <h3>原文定位</h3><blockquote>{String(sourceLocator.quote ?? "来源引用缺失")}</blockquote>
            {packageSource.assetVerification === "server_hash_verified" ? <figure className="quality-review-source-page"><img src={apiResourceUrl(`/api/quality-reviews/cases/${encodeURIComponent(detail.caseId)}/source-page`)} alt={`教材 PDF 第 ${detail.sourcePage} 页`} /><figcaption>服务端已按受管素材编号、PDF 指纹、PDF 页码和引用核验原教材页面。案例仍待人工复核。</figcaption></figure> : <div className="quality-review-source-gate" role="status"><strong>原教材 PDF 尚未连接</strong><p>请先通过现有教材上传流程登记原 PDF，并在复核包中关联上传 ID。未能从受管原件核验的决定只会保存为临时意见。</p></div>}
            <div className="quality-review-source-gate" role="status"><strong>人工决定仍由你提交</strong><p>预期答案和机检结果不代表教材正确。公开教材案例不计入人工金标准，身份未验证的意见也不表示独立双审。</p></div>
            <div className="quality-review-fingerprints"><span>来源 {detail.sourceFingerprint}</span><span>输入 {detail.inputFingerprint}</span><span>输出 {detail.outputFingerprint}</span></div>
          </section>

          <section className="panel quality-review-input"><h2>案例输入与真实 OCR 记录</h2><ReviewSummary value={detail.input} kind="input" /></section>

          <section className="quality-review-comparison">
            <article className="panel"><h2>预期结果</h2><ReviewSummary value={detail.expected} kind="expected" /></article>
            <article className="panel"><h2>实际观察</h2><ReviewSummary value={detail.actual} kind="actual" /></article>
          </section>

          <section className="panel quality-review-revisions">
            <h2>版本历史</h2>
            <p>修订会追加新版本；旧版决定仍绑定原有指纹，不会被覆盖。</p>
            <details><summary>查看 {detail.revisions.length} 个不可变版本</summary>
              {detail.revisions.map((revision) => <article className="quality-review-revision" key={revision.recordVersion}>
                <strong>版本 {revision.recordVersion}</strong><span>作者 {revision.createdBy} · {new Date(revision.createdAt * 1000).toLocaleString()}</span>
                <div>输入 {revision.inputFingerprint} · 预期 {revision.expected && JSON.stringify(revision.expected)} · 实际 {revision.outputFingerprint}</div>
              </article>)}
            </details>
            <QualityReviewRevisionEditor key={`${detail.caseId}:${detail.recordVersion}`} detail={detail} onSubmit={reviseCase} busy={busy || transitioning} />
          </section>

          <section className="panel quality-review-decision">
            <h2>{packageSource.assetVerification === "server_hash_verified" ? "提交你的最终单人教材决定" : "记录待核验意见"}</h2><p>当前身份未验证为独立复核者。请填写显示姓名和依据；不同姓名不会被视为不同审阅者。</p>
            {detail.decisions.map((item) => <article className="quality-review-prior-decision" key={item.reviewId}><strong>{item.displayName || item.actorId} · {item.stage === "provisional" ? "临时" : "最终"}{decisionLabels[item.decision]}</strong><span>版本 {item.reviewedRecordVersion} · {item.stage === "provisional" ? "原页未核验" : "原页已核验"} · {new Date(item.createdAt * 1000).toLocaleString()}</span>{item.claimedQuote && <blockquote>用户自报摘录（未由服务端核验）：{item.claimedQuote}</blockquote>}{item.verifiedQuote && <blockquote>服务端核对摘录：{item.verifiedQuote}</blockquote>}<p>{item.rationale}</p></article>)}
            <div className="quality-review-decision-form">
              <label>显示姓名<input value={displayName} maxLength={128} onChange={(event) => setDisplayName(event.target.value)} placeholder="仅作显示，身份未验证" /></label>
              <label>意见<select value={decision} onChange={(event) => setDecision(event.target.value as typeof decision)}><option value="accepted">初步认可</option><option value="rejected">初步拒绝</option><option value="needs_changes">待补充依据</option></select></label>
              <label>复核理由<textarea rows={4} value={rationale} onChange={(event) => setRationale(event.target.value)} placeholder="说明你查看了哪些版本信息以及仍需核对的内容" /></label>
              <label>{packageSource.assetVerification === "server_hash_verified" ? "原文核对摘录（系统会与受管教材原页比对）" : "用户自报原文摘录（未接入教材原页，不会标为已核验）"}<textarea rows={2} value={verifiedQuote} onChange={(event) => setVerifiedQuote(event.target.value)} placeholder="复制原页中与你的决定相关的准确文字" /></label>
              <button type="button" className="quality-review-primary" disabled={!rationale.trim() || busy || transitioning} onClick={() => void saveDecision()}>{busy ? "保存中…" : (verifiedQuote.trim() && packageSource.assetVerification === "server_hash_verified") || detail.sourceVerification === "human_quote_verified" ? "提交最终单人决定" : "保存待核验意见"}</button>
            </div>
          </section>
        </>}
      </section>
    </div>}
    {detail && <QualityReviewRunsPanel cases={cases} />}
    <footer className="quality-review-footer"><button type="button" onClick={() => navigate("/studio")}>返回教材与课程</button></footer>
  </main>;
}

function ReviewSummary({ value, kind }: { value: Record<string, unknown>; kind: "input" | "expected" | "actual" }) {
  const questionNumbers = stringList(value.questionNumbers);
  const acceptedAnswers = stringList(value.acceptedAnswers);
  const requiredFragments = stringList(value.requiredSourceFragments);
  const evidence = typeof value.evidenceQuote === "string" ? value.evidenceQuote : "";
  const checks = value.checks && typeof value.checks === "object" ? value.checks as Record<string, unknown> : {};
  const failedChecks = Object.entries(checks).filter(([, passed]) => passed === false).map(([name]) => name);
  const production = value.productionEvaluation && typeof value.productionEvaluation === "object" ? value.productionEvaluation as Record<string, unknown> : {};
  const resultText = typeof production.resultText === "string" ? production.resultText : "";
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
      <h3>真实 OCR 执行记录（待人工核对）</h3>
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
      {isRecord(value.productionEvaluation) && typeof value.productionEvaluation.assessment === "string" && <li>评测结果：{value.productionEvaluation.assessment}{typeof value.productionEvaluation.evidenceVerdict === "string" ? ` · 证据 ${value.productionEvaluation.evidenceVerdict}` : ""}</li>}
      {failedChecks.length > 0 && <li className="quality-review-failed-check">机检失败项：{failedChecks.join("、")}</li>}
      {!questionNumbers.length && !acceptedAnswers.length && !requiredFragments.length && !evidence && !resultText && !question && !passage && !providedAnswer && !ocrObservation && <li>{kind === "input" ? "未提供题目或段落输入。" : kind === "actual" ? "未提供可直接比较的结果字段。" : "未提供结构化预期结果。"}</li>}
    </ul>
    {limitations && <p className="quality-review-limitations">机检局限：{limitations}</p>}
    <details className="quality-review-raw-data"><summary>查看完整记录</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>
  </>;
}

function QualityReviewRevisionEditor({ detail, onSubmit, busy }: {
  detail: import("../../api/qualityReviews").QualityReviewCase;
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
  return <details className="quality-review-revision-editor"><summary>创建新案例版本</summary>
    <p>编辑 JSON 会产生新快照并保留旧决定。公开教材仍不计入人工金标准。</p>
    {(["input", "expected", "actual"] as const).map((key) => <label key={key}>{key === "input" ? "案例输入" : key === "expected" ? "预期结果" : "实际观察"}<textarea rows={7} value={snapshot[key]} onChange={(event) => setSnapshot((current) => ({ ...current, [key]: event.target.value }))} /></label>)}
    {error && <p className="quality-review-alert" role="alert">{error}</p>}
    <button type="button" className="quality-review-primary" disabled={busy} onClick={() => void submit()}>{busy ? "保存中…" : "追加为新版本"}</button>
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
