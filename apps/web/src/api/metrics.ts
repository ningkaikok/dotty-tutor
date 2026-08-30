import type { GeneratedSuccess } from "./client";
import { DEMO_LEARNER_ID, parse } from "./client";

type MetricsResponse = GeneratedSuccess<"get_model_call_metrics_api_metrics_model_calls_get">;
type LearningCostReportResponse = GeneratedSuccess<"get_learning_cost_report_api_reports_learning_cost_get">;

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

export interface ModelCallMetricsSummary {
  logicalCalls: number;
  failures: number;
  failureRate: number | null;
  avgDurationMs: number | null;
  totalPromptTokens: number | null;
  totalOutputTokens: number | null;
  tokenMeasuredCalls: number;
  tokenCoverageRate: number | null;
}

export interface LearningFunnelSnapshot {
  learnerId: string;
  mistakes: { imported: number; confirmed: number; confirmationRate: number | null };
  tutoring: { confirmedMistakes: number; threadsStarted: number };
  verification: { answeredVariations: number; correctVariations: number; passRate: number | null };
  review: { scheduledTasks: number; completedTasks: number; completionRate: number | null };
  learningEffect: {
    sameKnowledgePointReerrorCount: number;
    sameKnowledgePointReerrorDenominator: number;
    sameKnowledgePointReerrorRate: number | null;
  };
}

export interface LearningCostReport {
  learnerId: string;
  days: number;
  generatedAt: number;
  scope: { learning: "learner_cumulative"; modelCalls: "global_rolling_window"; costUnit: "proxy_only" };
  learning: LearningFunnelSnapshot;
  modelCost: { summary: ModelCallMetricsSummary; items: ModelCallMetricRow[] };
  limitations: string[];
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

/** 拉取学习效果与模型成本代理指标的联合报告。 */
export async function loadLearningCostReport(days = 7, learnerId = DEMO_LEARNER_ID): Promise<LearningCostReport> {
  const params = new URLSearchParams({ learnerId, days: String(days) });
  const response = await fetch(`/api/reports/learning-cost?${params.toString()}`, { cache: "no-store" });
  const payload = await parse<LearningCostReportResponse>(response);
  return payload as unknown as LearningCostReport;
}
