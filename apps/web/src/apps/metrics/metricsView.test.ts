import { describe, expect, it } from "vitest";
import { failureRate, formatDuration, formatRate, formatTokens, toRowViews, toSummaryView } from "./metricsView";
import type { ModelCallMetricRow, ModelCallMetricsSummary } from "../../api/metrics";

function row(overrides: Partial<ModelCallMetricRow>): ModelCallMetricRow {
  return {
    runtime: "generation",
    task: "lesson-generation",
    provider: "ollama",
    model: "qwen2.5:7b",
    calls: 10,
    failures: 1,
    avgDurationMs: 1500,
    totalOutputTokens: 2500,
    ...overrides,
  };
}

describe("failureRate", () => {
  it("分母为零返回 null 而不是 0", () => {
    expect(failureRate(0, 0)).toBeNull();
  });

  it("按千分位精度返回百分比", () => {
    expect(failureRate(10, 1)).toBe(10);
    expect(failureRate(3, 1)).toBe(33.3);
  });
});

describe("formatDuration", () => {
  it("毫秒与秒自动切换", () => {
    expect(formatDuration(850)).toBe("850ms");
    expect(formatDuration(1500)).toBe("1.5s");
    expect(formatDuration(null)).toBe("暂无数据");
  });
});

describe("report formatting", () => {
  it("保留空值语义并按比例格式化", () => {
    expect(formatRate(null)).toBe("暂无数据");
    expect(formatRate(0.375)).toBe("37.5%");
    expect(formatTokens(null)).toBe("暂无数据");
    const summary: ModelCallMetricsSummary = {
      logicalCalls: 4,
      failures: 1,
      failureRate: 0.25,
      avgDurationMs: null,
      totalPromptTokens: null,
      totalOutputTokens: 200,
      tokenMeasuredCalls: 2,
      tokenCoverageRate: 0.5,
    };
    expect(toSummaryView(summary)).toMatchObject({
      failureRateLabel: "25.0%",
      durationLabel: "暂无数据",
      promptTokensLabel: "暂无数据",
      tokenCoverageLabel: "50.0%",
      partialTokenLabel: "2 / 4 次调用",
    });
  });
});

describe("toRowViews", () => {
  it("补充视图字段并按调用量降序排序", () => {
    const views = toRowViews([
      row({ calls: 3, totalOutputTokens: 0 }),
      row({ calls: 12 }),
    ]);
    expect(views[0].calls).toBe(12);
    expect(views[0].failureRate).toBe(8.3);
    expect(views[1].calls).toBe(3);
    expect(views[1].tokenLabel).toBe("—");
  });
});
