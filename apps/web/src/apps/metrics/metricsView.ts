/** 指标表格的纯视图转换；与数据拉取分离，便于单测。 */

import type { ModelCallMetricRow, ModelCallMetricsSummary } from "../../api/metrics";

export interface MetricRowView extends ModelCallMetricRow {
  failureRate: number | null;
  durationLabel: string;
  tokenLabel: string;
}

/** 失败率：分母为零返回 null（界面显示"—"），不用 0 冒充。 */
export function failureRate(calls: number, failures: number): number | null {
  if (calls <= 0) return null;
  return Math.round((failures / calls) * 1000) / 10;
}

export function formatDuration(ms: number | null): string {
  if (ms === null) return "暂无数据";
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms)}ms`;
}

export function formatRate(rate: number | null): string {
  return rate === null ? "暂无数据" : `${(rate * 100).toFixed(1)}%`;
}

export function formatTokens(tokens: number | null): string {
  return tokens === null ? "暂无数据" : tokens.toLocaleString();
}

export interface MetricSummaryView {
  logicalCallsLabel: string;
  failuresLabel: string;
  failureRateLabel: string;
  durationLabel: string;
  promptTokensLabel: string;
  outputTokensLabel: string;
  tokenCoverageLabel: string;
  partialTokenLabel: string;
}

export function toSummaryView(summary: ModelCallMetricsSummary): MetricSummaryView {
  return {
    logicalCallsLabel: summary.logicalCalls.toLocaleString(),
    failuresLabel: summary.failures.toLocaleString(),
    failureRateLabel: formatRate(summary.failureRate),
    durationLabel: formatDuration(summary.avgDurationMs),
    promptTokensLabel: formatTokens(summary.totalPromptTokens),
    outputTokensLabel: formatTokens(summary.totalOutputTokens),
    tokenCoverageLabel: formatRate(summary.tokenCoverageRate),
    partialTokenLabel: `${summary.tokenMeasuredCalls.toLocaleString()} / ${summary.logicalCalls.toLocaleString()} 次调用`,
  };
}

export function toRowViews(items: ModelCallMetricRow[]): MetricRowView[] {
  return items
    .map((item) => ({
      ...item,
      failureRate: failureRate(item.calls, item.failures),
      durationLabel: formatDuration(item.avgDurationMs),
      tokenLabel: item.totalOutputTokens > 0 ? item.totalOutputTokens.toLocaleString() : "—",
    }))
    .sort((a, b) => b.calls - a.calls);
}
