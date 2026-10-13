import { useEffect, useMemo, useState } from "react";
import { Button, Notice, SelectField, TextAreaField } from "../../components/ui";
import type { QualityReviewRunCase } from "../../api/qualityReviewRuns";
import type { QualityReviewCaseSummary } from "../../api/qualityReviews";
import { useQualityReviewRuns } from "./useQualityReviewRuns";
import "./qualityReviewRuns.css";

type Props = { cases: QualityReviewCaseSummary[] };

export function QualityReviewRunsPanel({ cases: availableCases }: Props) {
  const {
    models, run, error, notice, busy, statistics, goldExport,
    refresh, start, cancel, rate, loadStatistics, loadGoldExport,
  } = useQualityReviewRuns();
  const choices = useMemo(() => models.flatMap((provider) => provider.available
    ? provider.models.map((model) => ({ provider: provider.provider, model, label: `${provider.label} · ${model}` }))
    : []), [models]);
  const [selectedCaseIds, setSelectedCaseIds] = useState<string[]>([]);
  const [instruction, setInstruction] = useState("按案例输入提取题号和必须保留的原文片段。");
  const [baseline, setBaseline] = useState("");
  const [candidate, setCandidate] = useState("");
  const caseById = useMemo(() => new Map(availableCases.map((item) => [item.caseId, item])), [availableCases]);

  useEffect(() => {
    setSelectedCaseIds((current) => current.filter((id) => caseById.has(id)).slice(0, 6));
  }, [caseById]);
  useEffect(() => {
    if (!choices.length) return;
    if (!choices.some((item) => `${item.provider}/${item.model}` === baseline)) setBaseline(`${choices[0].provider}/${choices[0].model}`);
    if (!choices.some((item) => `${item.provider}/${item.model}` === candidate) || candidate === baseline) {
      setCandidate(`${(choices.find((item) => `${item.provider}/${item.model}` !== baseline) ?? choices[1] ?? choices[0]).provider}/${(choices.find((item) => `${item.provider}/${item.model}` !== baseline) ?? choices[1] ?? choices[0]).model}`);
    }
  }, [choices, baseline, candidate]);

  const selectModel = (key: string) => {
    const [provider, ...model] = key.split("/");
    return { provider, model: model.join("/") };
  };
  const onStart = () => {
    if (selectedCaseIds.length < 1 || !baseline || !candidate || baseline === candidate) return;
    void start({
      caseIds: selectedCaseIds,
      taskInstruction: instruction,
      baseline: selectModel(baseline),
      candidate: selectModel(candidate),
      perCallTimeoutSeconds: 30,
      totalBudgetSeconds: 240,
      maxOutputTokens: 500,
    });
  };

  return <section className="quality-runs panel" aria-label="有界模型对比">
    <div className="quality-runs-heading"><div><span className="eyebrow">持久化运行 · 匿名 A/B</span><h2>模型对比与人工评分</h2></div>
      <Button onClick={() => void refresh()} disabled={busy}>刷新</Button></div>
    <p className="quality-runs-description">每次最多运行 6 案，单次模型调用最多 60 秒，总时间最多 300 秒。人工评分前隐藏模型身份；失败臂不能评分或排名。</p>
    <div className="quality-runs-launch">
      <fieldset><legend>选择冻结案例（最多 6 项）</legend>
        {!availableCases.length ? <p>请先打开来源包中的案例。</p> : availableCases.map((item) => <label key={item.caseId}>
          <input type="checkbox" checked={selectedCaseIds.includes(item.caseId)} disabled={!selectedCaseIds.includes(item.caseId) && selectedCaseIds.length >= 6}
            onChange={(event) => setSelectedCaseIds((current) => event.target.checked ? [...current, item.caseId].slice(0, 6) : current.filter((id) => id !== item.caseId))} />
          <span>{item.externalCaseId} · {item.subject} · PDF 第 {item.sourcePage} 页 · 版本 {item.recordVersion}</span>
        </label>)}
      </fieldset>
      <TextAreaField label="任务要求" rows={3} value={instruction} onChange={(event) => setInstruction(event.target.value)} maxLength={4000} />
      <SelectField label="基线模型" value={baseline} onChange={(event) => setBaseline(event.target.value)}>{choices.map((item) => <option key={`${item.provider}/${item.model}`} value={`${item.provider}/${item.model}`}>{item.label}</option>)}</SelectField>
      <SelectField label="候选模型" value={candidate} onChange={(event) => setCandidate(event.target.value)}>{choices.map((item) => <option key={`${item.provider}/${item.model}`} value={`${item.provider}/${item.model}`}>{item.label}</option>)}</SelectField>
      <Button variant="primary" className="quality-review-primary" disabled={busy || selectedCaseIds.length === 0 || !choices.length || baseline === candidate} onClick={onStart}>启动有界运行</Button>
    </div>
    {error && <Notice tone="danger" className="quality-review-alert">{error}</Notice>}
    {notice && <Notice tone="info" className="quality-review-notice">{notice}</Notice>}
    {run && <RunResult run={run} caseById={caseById} busy={busy} onCancel={() => void cancel()} onRate={(value) => void rate(value)} onStatistics={() => void loadStatistics()} />}
    {statistics && <section className="quality-runs-result"><h3>探索性统计</h3><pre>{JSON.stringify(statistics, null, 2)}</pre><p>此运行最多 6 案，统计仅供单次探索，不代表正式模型排名。</p></section>}
    <section className="quality-runs-gold"><div><h3>正式人工金标准</h3><p>需要受信人工作者、同一冻结版本上两位不同且身份已验证的最终审核者，并达到 50 案及覆盖门槛。</p></div>
      <Button onClick={() => void loadGoldExport()}>检查导出门槛</Button>
      {goldExport && <pre>{JSON.stringify(goldExport, null, 2)}</pre>}
    </section>
  </section>;
}

function RunResult({ run, caseById, busy, onCancel, onRate, onStatistics }: {
  run: NonNullable<ReturnType<typeof useQualityReviewRuns>["run"]>;
  caseById: Map<string, QualityReviewCaseSummary>;
  busy: boolean;
  onCancel: () => void;
  onRate: (value: { caseId: string; aPass: boolean; bPass: boolean; preferred: "A" | "B" | "tie" | "neither"; rationale: string }) => void;
  onStatistics: () => void;
}) {
  return <section className="quality-runs-result" aria-live="polite">
    <div className="quality-runs-heading"><div><h3>运行 {run.status === "queued" ? "排队中" : run.status === "running" ? "执行中" : run.status === "completed" ? "已完成" : run.status === "cancelled" ? "已取消" : "失败"}</h3>
      <p>记录 {run.runId.slice(0, 12)}… · {run.cases.length} 案 · 每次调用最多 {run.perCallTimeoutSeconds}s</p></div>
      {run.status === "queued" || run.status === "running" ? <Button onClick={onCancel} disabled={busy}>取消运行</Button> : <Button onClick={onStatistics}>查看统计</Button>}
    </div>
    <p className="quality-runs-task"><strong>本次任务要求：</strong>{run.taskInstruction}</p>
    {run.models && <p>揭盲：基线 {run.models.baseline.provider}/{run.models.baseline.model} · 候选 {run.models.candidate.provider}/{run.models.candidate.model}</p>}
    {run.error && <Notice tone="danger" className="quality-review-alert">{run.error.message}</Notice>}
    {run.cases.map((item) => <RunCase key={item.caseId} item={item} caseSummary={caseById.get(item.caseId)} run={run} busy={busy} onRate={onRate} />)}
  </section>;
}

function RunCase({ item, caseSummary, run, busy, onRate }: {
  item: QualityReviewRunCase;
  caseSummary: QualityReviewCaseSummary | undefined;
  run: NonNullable<ReturnType<typeof useQualityReviewRuns>["run"]>;
  busy: boolean;
  onRate: (value: { caseId: string; aPass: boolean; bPass: boolean; preferred: "A" | "B" | "tie" | "neither"; rationale: string }) => void;
}) {
  const existing = run.ratings.find((rating) => rating.caseId === item.caseId);
  const a = item.arms.A;
  const b = item.arms.B;
  const question = firstText(item.snapshot.input, ["question", "prompt", "questionText"]);
  const passage = firstText(item.snapshot.input, ["passage", "ocrText"]);
  return <article className="quality-runs-case">
    <h4>案例 {item.ordinal} · {caseSummary?.externalCaseId ?? item.caseId} · 版本 {item.recordVersion}</h4>
    <p>来源：{item.sourceKind} · PDF 第 {item.snapshot.sourcePage ?? caseSummary?.sourcePage ?? "未知"} 页</p>
    <section className="quality-runs-frozen-case" aria-label="冻结案例内容">
      <h5>供模型处理的案例</h5>
      {question && <p><strong>题目：</strong>{question}</p>}
      {passage && <blockquote><strong>原文：</strong>{passage}</blockquote>}
      <h5>冻结预期结果</h5>
      <pre>{JSON.stringify(item.snapshot.expected, null, 2)}</pre>
      <details><summary>查看完整冻结输入和来源指纹</summary><pre>{JSON.stringify({
        source: item.snapshot.source,
        sourceLocator: item.snapshot.sourceLocator,
        sourceFingerprint: item.sourceFingerprint,
        inputFingerprint: item.inputFingerprint,
        outputFingerprint: item.outputFingerprint,
        input: item.snapshot.input,
        actual: item.snapshot.actual,
      }, null, 2)}</pre></details>
    </section>
    <div className="quality-runs-pair">
      <ArmView label="A" arm={a} />
      <ArmView label="B" arm={b} />
    </div>
    {a?.status === "succeeded" && b?.status === "succeeded" && !existing && <RatingForm caseId={item.caseId} busy={busy} onRate={onRate} />}
    {existing && <p role="status">已保存你的评分：A {existing.aPass ? "通过" : "未通过"} · B {existing.bPass ? "通过" : "未通过"}。身份验证：{existing.identityVerified ? "已验证" : "未验证"}。</p>}
  </article>;
}

function firstText(value: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) if (typeof value[key] === "string" && value[key]) return value[key] as string;
  return "";
}

function ArmView({ label, arm }: { label: "A" | "B"; arm: QualityReviewRunCase["arms"]["A"] }) {
  return <section className="quality-runs-arm"><h5>匿名输出 {label} · {arm?.status ?? "等待中"}</h5>
    {arm?.actualProvider && arm.actualModel && <p>揭盲模型：{arm.actualProvider}/{arm.actualModel}</p>}
    {arm?.output ? <pre>{JSON.stringify(arm.output, null, 2)}</pre> : <p>{arm?.error?.message ?? "尚无输出"}</p>}
    {arm?.durationMs != null && <small>用时 {(arm.durationMs / 1000).toFixed(1)} 秒 · Token {arm.promptTokens ?? "未知"}/{arm.outputTokens ?? "未知"} · 成本 {arm.cost ?? "未知"}</small>}
  </section>;
}

function RatingForm({ caseId, busy, onRate }: {
  caseId: string;
  busy: boolean;
  onRate: (value: { caseId: string; aPass: boolean; bPass: boolean; preferred: "A" | "B" | "tie" | "neither"; rationale: string }) => void;
}) {
  const [aPass, setAPass] = useState(true);
  const [bPass, setBPass] = useState(true);
  const [preferred, setPreferred] = useState<"A" | "B" | "tie" | "neither">("tie");
  const [rationale, setRationale] = useState("");
  return <form className="quality-runs-rating" onSubmit={(event) => { event.preventDefault(); onRate({ caseId, aPass, bPass, preferred, rationale }); }}>
    <h5>提交你的匿名评分</h5>
    <label><input type="checkbox" checked={aPass} onChange={(event) => setAPass(event.target.checked)} />A 满足任务要求</label>
    <label><input type="checkbox" checked={bPass} onChange={(event) => setBPass(event.target.checked)} />B 满足任务要求</label>
    <SelectField label="偏好" value={preferred} onChange={(event) => setPreferred(event.target.value as typeof preferred)}><option value="A">A</option><option value="B">B</option><option value="tie">相同</option><option value="neither">都不合格</option></SelectField>
    <TextAreaField label="评分理由" required minLength={1} maxLength={2000} value={rationale} onChange={(event) => setRationale(event.target.value)} />
    <Button type="submit" variant="primary" className="quality-review-primary" disabled={busy || !rationale.trim()}>保存评分</Button>
  </form>;
}
