// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ModelMetricsApp } from "./ModelMetricsApp";

const report = {
  learnerId: "local-demo",
  days: 7,
  generatedAt: 1,
  scope: { learning: "learner_cumulative", modelCalls: "global_rolling_window", costUnit: "proxy_only" },
  learning: {
    learnerId: "local-demo",
    mistakes: { imported: 8, confirmed: 6, confirmationRate: 0.75 },
    tutoring: { confirmedMistakes: 5, threadsStarted: 4 },
    verification: { answeredVariations: 3, correctVariations: 2, passRate: 2 / 3 },
    review: { scheduledTasks: 2, completedTasks: 1, completionRate: 0.5 },
    learningEffect: { sameKnowledgePointReerrorCount: 1, sameKnowledgePointReerrorDenominator: 4, sameKnowledgePointReerrorRate: 0.25 },
  },
  modelCost: {
    summary: {
      logicalCalls: 12,
      failures: 1,
      failureRate: 1 / 12,
      avgDurationMs: 1250,
      totalPromptTokens: 3000,
      totalOutputTokens: 1200,
      tokenMeasuredCalls: 10,
      tokenCoverageRate: 10 / 12,
    },
    items: [{
      runtime: "tutor",
      task: "hint",
      provider: "mock",
      model: "test-model",
      calls: 12,
      failures: 1,
      avgDurationMs: 1250,
      totalOutputTokens: 1200,
    }],
  },
  limitations: [],
};

function renderMetrics() {
  return render(
    <MemoryRouter initialEntries={["/studio/metrics"]}>
      <ModelMetricsApp />
    </MemoryRouter>,
  );
}

describe("ModelMetricsApp", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => report,
    })));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the report and reloads it when the window changes", async () => {
    const user = userEvent.setup();
    renderMetrics();

    expect(screen.getByRole("status")).toHaveTextContent("报告加载中");
    expect(await screen.findByRole("heading", { name: "学习效果与模型成本报告" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "学习效果漏斗" })).toBeVisible();
    expect(screen.getByText("模型调用边界指标（最近 7 天）")).toBeVisible();
    expect(screen.getByText("test-model")).toBeVisible();

    await user.selectOptions(screen.getByRole("combobox", { name: "模型统计窗口天数" }), "30");
    await waitFor(() => expect(fetch).toHaveBeenLastCalledWith(
      "/api/reports/learning-cost?learnerId=local-demo&days=30",
      { cache: "no-store" },
    ));
  });
});
