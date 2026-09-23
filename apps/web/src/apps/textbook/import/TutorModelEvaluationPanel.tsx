import { useEffect, useState } from "react";
import type { ModelCatalog, ModelProvider, TutorModelEvaluation, TutorModelRef } from "../../../types/index";

interface TutorModelEvaluationPanelProps {
  models: ModelCatalog | null;
  evaluation: TutorModelEvaluation | null;
  evaluationLoading: boolean;
  evaluationError: string;
  disabled: boolean;
  selectionLoading: boolean;
  onEvaluate: (provider: Exclude<ModelProvider, "mock">, model: string) => void;
  onApply: (provider: Exclude<ModelProvider, "mock">, model: string) => void;
}

/** Compare the current tutor runtime with a candidate before applying a global demo setting. */
export function TutorModelEvaluationPanel({
  models,
  evaluation,
  evaluationLoading,
  evaluationError,
  disabled,
  selectionLoading,
  onEvaluate,
  onApply,
}: TutorModelEvaluationPanelProps) {
  const currentTutorId = models ? `${models.selected.provider}::${models.selected.model}` : "";
  const choices = models?.providers.flatMap((provider) => provider.models
    .filter((model) => provider.id !== "mock" && provider.available && provider.modelDetails?.some((detail) => (
      detail.name === model && detail.roles.includes("tutoring") && detail.health.healthy
    )))
    .map((model) => ({
      id: `${provider.id}::${model}`,
      provider: provider.id,
      model,
      label: `${provider.label} · ${model}`,
    }))) ?? [];
  const preferredCandidate = choices.find((choice) => choice.id === currentTutorId)?.id ?? choices[0]?.id ?? "";
  const [candidateTutorId, setCandidateTutorId] = useState(preferredCandidate);
  useEffect(() => setCandidateTutorId(preferredCandidate), [preferredCandidate]);

  const selectedCandidate = choices.find((choice) => choice.id === candidateTutorId);
  const candidate: TutorModelRef | null = selectedCandidate && selectedCandidate.provider !== "mock"
    ? { provider: selectedCandidate.provider, model: selectedCandidate.model }
    : null;
  const currentTutorAvailable = choices.some((choice) => choice.id === currentTutorId);
  const matchesEvaluation = Boolean(
    evaluation?.status === "completed"
    && evaluation.baseline.provider === models?.selected.provider
    && evaluation.baseline.model === models?.selected.model
    && evaluation.candidate.provider === candidate?.provider
    && evaluation.candidate.model === candidate?.model,
  );
  const summary = evaluation?.summary;
  const difference = summary?.pairedStrictReferenceDifference;

  return (
    <>
      <div className="tutor-label">
        <strong>陪练模型切换前评测</strong>
        <small>学生继续使用当前服务端模型。先比较当前模型和候选模型，评测完成后再应用切换。</small>
      </div>
      <div className="tutor-current-model">
        当前模型：{models ? `${models.selected.provider} · ${models.selected.model}` : "正在读取…"}
      </div>
      {models && models.selected.provider !== "mock" && !currentTutorAvailable && (
        <p className="tutor-evaluation-error" role="alert">当前陪练模型不可用或不支持陪练任务，刷新模型目录后再评测。</p>
      )}
      <select
        className="tutor-select"
        aria-label="候选陪练模型"
        value={candidateTutorId}
        disabled={!models || disabled || selectionLoading || evaluationLoading}
        onChange={(event) => setCandidateTutorId(event.target.value)}
      >
        {!models && <option value="">正在读取陪练模型…</option>}
        {models && choices.length === 0 && <option value="">没有健康且支持陪练的模型</option>}
        {choices.map((choice) => (
          <option key={choice.id} value={choice.id}>
            {choice.label}{choice.id === currentTutorId ? " · 当前" : ""}
          </option>
        ))}
      </select>
      <div className="tutor-evaluation-actions">
        <button
          type="button"
          className="tutor-evaluation-button"
          disabled={!candidate || !currentTutorAvailable || models?.selected.provider === "mock" || candidateTutorId === currentTutorId || disabled || selectionLoading || evaluationLoading}
          onClick={() => candidate && onEvaluate(candidate.provider, candidate.model)}
        >
          {evaluationLoading ? "正在评测…" : "比较当前与候选模型"}
        </button>
        {evaluation?.status === "completed" && candidate && (
          <button
            type="button"
            className="tutor-evaluation-button primary"
            disabled={!matchesEvaluation || disabled || selectionLoading}
            title={matchesEvaluation ? "将候选模型设为后续学生陪练使用的模型" : "请对当前模型组合重新评测后应用"}
            onClick={() => onApply(candidate.provider, candidate.model)}
          >
            应用候选模型
          </button>
        )}
      </div>
      <p className="tutor-evaluation-note">
        评测使用 50 条合成草案，每轮比较会产生约 100 次模型调用，可能耗时并消耗云端额度。草案不计入正式人工金标准；精确匹配只是参考指标，不能替代人工语义判断。报告暂存在 API 进程内，服务重启后会清空。
      </p>
      {evaluationLoading && evaluation && (
        <p className="tutor-evaluation-progress" role="status" aria-live="polite">
          {evaluation.status === "queued" ? "评测排队中" : "评测进行中"}：已完成 {evaluation.completedCases}/{evaluation.totalCases} 组对照
        </p>
      )}
      {evaluationError && <p className="tutor-evaluation-error" role="alert">{evaluationError}</p>}
      {evaluation?.status === "completed" && summary && (
        <section className="tutor-evaluation-report" aria-label="陪练模型评测结果">
          <h3>模型对照结果</h3>
          <p className="tutor-evaluation-report-note">
            {evaluation.baseline.provider}/{evaluation.baseline.model} 对比 {evaluation.candidate.provider}/{evaluation.candidate.model} · {evaluation.totalCases} 组配对草案
          </p>
          {!matchesEvaluation && <p className="tutor-evaluation-stale" role="status">当前模型或候选项已变化；这份报告不能用于应用当前选择。</p>}
          <div className="tutor-evaluation-table-wrap">
            <table>
              <thead><tr><th scope="col">指标</th><th scope="col">当前模型</th><th scope="col">候选模型</th></tr></thead>
              <tbody>
                <tr><th scope="row">结构输出成功</th><td>{summary.baseline.successfulCalls}/{summary.baseline.cases}</td><td>{summary.candidate.successfulCalls}/{summary.candidate.cases}</td></tr>
                <tr><th scope="row">整题精确匹配</th><td>{summary.baseline.strictReferenceCases}/{summary.baseline.cases}（{Math.round(summary.baseline.strictReferenceCaseRate * 100)}%）</td><td>{summary.candidate.strictReferenceCases}/{summary.candidate.cases}（{Math.round(summary.candidate.strictReferenceCaseRate * 100)}%）</td></tr>
                <tr><th scope="row">字段精确匹配</th><td>{summary.baseline.exactReferenceFields}/{summary.baseline.referenceFields}</td><td>{summary.candidate.exactReferenceFields}/{summary.candidate.referenceFields}</td></tr>
                <tr><th scope="row">延迟 P50 / P95</th><td>{formatLatency(summary.baseline.latencyMs.p50)} / {formatLatency(summary.baseline.latencyMs.p95)}</td><td>{formatLatency(summary.candidate.latencyMs.p50)} / {formatLatency(summary.candidate.latencyMs.p95)}</td></tr>
                <tr><th scope="row">已知 Token 用量</th><td>{formatTokens(summary.baseline.tokenUsage.promptTokens, summary.baseline.tokenUsage.outputTokens)}</td><td>{formatTokens(summary.candidate.tokenUsage.promptTokens, summary.candidate.tokenUsage.outputTokens)}</td></tr>
              </tbody>
            </table>
          </div>
          <p className="tutor-evaluation-report-note">
            配对差异（候选 - 当前）：{formatPercentage(difference?.difference)}，符号检验 p={difference?.signPValue?.toFixed(3) ?? "无数据"}，n={difference?.sampleCount ?? 0}。草案结果不代表正式统计结论。
          </p>
          <h4>按任务维度</h4>
          <div className="tutor-evaluation-table-wrap">
            <table>
              <thead><tr><th scope="col">维度</th><th scope="col">当前结构成功 / 精确匹配</th><th scope="col">候选结构成功 / 精确匹配</th></tr></thead>
              <tbody>{Object.entries(summary.baseline.dimensions).map(([dimension, baseline]) => {
                const candidateResult = summary.candidate.dimensions[dimension];
                return (
                  <tr key={dimension}>
                    <th scope="row">{dimension}</th>
                    <td>{baseline.successfulCalls}/{baseline.cases} / {baseline.strictReferenceCases}/{baseline.cases}</td>
                    <td>{candidateResult ? `${candidateResult.successfulCalls}/${candidateResult.cases} / ${candidateResult.strictReferenceCases}/${candidateResult.cases}` : "无数据"}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
          <details className="tutor-evaluation-cases">
            <summary>查看逐题输出与参考答案（{evaluation.results?.length ?? 0} 题）</summary>
            {evaluation.results?.map((result) => (
              <article key={result.caseId}>
                <h4>{result.caseId} · {result.taskDimension}</h4>
                <p><strong>输入</strong></p><pre>{JSON.stringify(result.input, null, 2)}</pre>
                <p><strong>评分 Rubric</strong></p><pre>{JSON.stringify(result.rubric, null, 2)}</pre>
                <p><strong>参考答案</strong></p><pre>{JSON.stringify(result.expected, null, 2)}</pre>
                <p><strong>当前模型输出（字段精确匹配）</strong></p><pre>{JSON.stringify({ output: result.baseline.output, matches: result.baseline.referenceFields, status: result.baseline.status, errorType: result.baseline.errorType }, null, 2)}</pre>
                <p><strong>候选模型输出（字段精确匹配）</strong></p><pre>{JSON.stringify({ output: result.candidate.output, matches: result.candidate.referenceFields, status: result.candidate.status, errorType: result.candidate.errorType }, null, 2)}</pre>
              </article>
            ))}
          </details>
        </section>
      )}
    </>
  );
}

function formatLatency(value: number | null): string {
  return value === null ? "无数据" : `${Math.round(value)} ms`;
}

function formatTokens(prompt: number | null, output: number | null): string {
  if (prompt === null && output === null) return "无数据";
  return `输入 ${prompt ?? "未知"} / 输出 ${output ?? "未知"}`;
}

function formatPercentage(value?: number): string {
  if (value === undefined || !Number.isFinite(value)) return "无数据";
  const points = value * 100;
  return `${points > 0 ? "+" : ""}${points.toFixed(1)} 个百分点`;
}
