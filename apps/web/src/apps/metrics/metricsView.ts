/** 指标表格的纯视图转换；与数据拉取分离，便于单测。 */

import type { ModelCallMetricRow } from "../../api/metrics";

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

export function formatDuration(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms)}ms`;
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
