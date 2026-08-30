import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { loadLearningCostReport, type LearningCostReport } from "../../api/metrics";
import { formatRate, formatTokens, toRowViews, toSummaryView, type MetricRowView } from "./metricsView";
import "./metrics.css";

const DAY_OPTIONS = [7, 14, 30] as const;

/**
 * 学习效果与模型成本代理指标面板（内容生产端专用，学生端不可见）。
 * 学习指标按学生累计，模型指标按全局滚动窗口聚合，不做学生级归因。
 */
export function ModelMetricsApp() {
  const navigate = useNavigate();
  const [days, setDays] = useState<number>(7);
  const [report, setReport] = useState<LearningCostReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async (windowDays: number) => {
    setLoading(true);
    setError("");
    try {
      setReport(await loadLearningCostReport(windowDays));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "报告加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh(days);
  }, [days, refresh]);

  const rows: MetricRowView[] = report ? toRowViews(report.modelCost.items) : [];
  const summary = report ? toSummaryView(report.modelCost.summary) : null;
  const learning = report?.learning;
  const stages = learning ? [
    ["错题导入", learning.mistakes.imported, null],
    ["人工确认", learning.mistakes.confirmed, learning.mistakes.confirmationRate],
    ["开始陪练", learning.tutoring.threadsStarted, null],
    ["变式验证", learning.verification.answeredVariations, learning.verification.passRate],
    ["复习完成", learning.review.completedTasks, learning.review.completionRate],
  ] as const : [];

  return (
    <main className="metrics-shell">
      <header className="import-header">
        <button className="route-back-button" onClick={() => navigate("/studio")}>← 返回工作台</button>
        <div className="brand-mark">D</div>
        <div>
          <strong>Dotty</strong>
          <span>学习效果与模型成本</span>
        </div>
        <span className="demo-badge">LOCAL DEMO</span>
      </header>

      <section className="panel metrics-panel" aria-label="学习效果与模型成本报告">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">CONTENT STUDIO · 只读聚合</span>
            <h2>学习效果与模型成本报告</h2>
            <p className="muted">
              学习数据按学生累计，模型数据按最近窗口全局聚合；成本仅为调用、耗时和 Token 代理指标。
            </p>
          </div>
          <label className="metrics-window">
            模型统计窗口
            <select
              value={days}
              onChange={(event) => setDays(Number(event.target.value))}
              aria-label="模型统计窗口天数"
            >
              {DAY_OPTIONS.map((option) => (
                <option key={option} value={option}>最近 {option} 天</option>
              ))}
            </select>
          </label>
        </div>

        <div className="metrics-body">
          {loading && <p role="status" className="muted">报告加载中…</p>}
          {error && <p role="alert" className="error-text">{error}</p>}
          {!loading && !error && report && learning && (
            <>
              <div className="metrics-scope-note" role="note">
                <strong>口径：</strong>学习效果为 {report.learnerId} 的累计快照；模型调用为最近 {report.days} 天的全局滚动窗口。
                逻辑调用数不等于 Provider 重试次数，不提供学生级模型成本归因，也不代表因果关系。
              </div>

              <div className="metrics-summary-grid" aria-label="模型汇总指标">
                {summary && [
                  ["逻辑调用", summary.logicalCallsLabel],
                  ["失败率", summary.failureRateLabel],
                  ["平均耗时", summary.durationLabel],
                  ["输入 tokens", summary.promptTokensLabel],
                  ["输出 tokens", summary.outputTokensLabel],
                  ["Token 覆盖率", summary.tokenCoverageLabel],
                ].map(([label, value]) => (
                  <div className="metrics-summary-card" key={label}>
                    <span>{label}</span>
                    <strong>{value}</strong>
                  </div>
                ))}
              </div>

              <section className="metrics-learning-section" aria-label="学习效果漏斗">
                <div className="metrics-section-heading">
                  <div>
                    <span className="eyebrow">LEARNING · 累计快照</span>
                    <h3>学习效果漏斗</h3>
                  </div>
                  <p className="muted">Token 已完整观测 {summary?.partialTokenLabel ?? "暂无数据"}</p>
                </div>
                <div className="metrics-funnel-grid">
                  {stages.map(([label, count, rate]) => (
                    <div className="metrics-funnel-card" key={label}>
                      <span>{label}</span>
                      <strong>{count.toLocaleString()}</strong>
                      <small>{rate === null ? "阶段计数" : `${formatRate(rate)} 阶段比率`}</small>
                    </div>
                  ))}
                </div>
                <div className="metrics-effect-row">
                  <span>验证正确率 <strong>{formatRate(learning.verification.passRate)}</strong></span>
                  <span>复习完成率 <strong>{formatRate(learning.review.completionRate)}</strong></span>
                  <span>同知识点再错率 <strong>{formatRate(learning.learningEffect.sameKnowledgePointReerrorRate)}</strong></span>
                </div>
              </section>

              <section aria-label="模型调用明细">
                <div className="metrics-section-heading">
                  <div>
                    <span className="eyebrow">MODEL · 最近窗口</span>
                    <h3>模型调用明细</h3>
                  </div>
                  <p className="muted">
                    Token 缺失不会按 0 计入：输入 {formatTokens(report.modelCost.summary.totalPromptTokens)}，输出 {formatTokens(report.modelCost.summary.totalOutputTokens)}
                  </p>
                </div>
                {rows.length === 0 && <p role="status" className="muted">窗口内没有模型调用记录。</p>}
                {rows.length > 0 && (
                  <div className="metrics-table-wrapper">
                    <table className="metrics-table">
                      <caption>模型调用边界指标（最近 {report.days} 天）</caption>
                      <thead>
                        <tr>
                          <th scope="col">runtime</th>
                          <th scope="col">task</th>
                          <th scope="col">provider</th>
                          <th scope="col">model</th>
                          <th scope="col">调用</th>
                          <th scope="col">失败</th>
                          <th scope="col">失败率</th>
                          <th scope="col">平均耗时</th>
                          <th scope="col">输出 tokens</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((row) => (
                          <tr key={`${row.runtime}/${row.task}/${row.provider}/${row.model}`}>
                            <td>{row.runtime}</td>
                            <td>{row.task}</td>
                            <td>{row.provider}</td>
                            <td>{row.model}</td>
                            <td>{row.calls}</td>
                            <td>{row.failures}</td>
                            <td>{row.failureRate === null ? "—" : `${row.failureRate}%`}</td>
                            <td>{row.durationLabel}</td>
                            <td>{row.tokenLabel}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            </>
          )}
        </div>
      </section>
    </main>
  );
}
