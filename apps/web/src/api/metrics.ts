import type { GeneratedSuccess } from "./client";
import { parse } from "./client";

type MetricsResponse = GeneratedSuccess<"get_model_call_metrics_api_metrics_model_calls_get">;

export interface ModelCallMetricRow {
  runtime: string;
  task: string;
  provider: string;
  model: string;
  calls: number;
  failures: number;
  avgDurationMs: number;
  totalOutputTokens: number;
}

export interface ModelCallMetricsSnapshot {
  days: number;
  items: ModelCallMetricRow[];
}

/** 拉取模型调用边界指标聚合快照（只读；窗口天数 1-90 由服务端钳制）。 */
export async function loadModelCallMetrics(days = 7): Promise<ModelCallMetricsSnapshot> {
  const response = await fetch(`/api/metrics/model-calls?days=${days}`, { cache: "no-store" });
  const payload = await parse<MetricsResponse>(response);
  // 后端返回 dict[str, Any]，生成的 OpenAPI 类型为 unknown；这里做运行时收窄。
  return {
    days: typeof payload.days === "number" ? payload.days : 7,
    items: (Array.isArray(payload.items) ? payload.items : []) as ModelCallMetricRow[],
  };
}
