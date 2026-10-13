// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QualityReviewInboxItem } from "../../api/qualityReviews";
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
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders the report and reloads it when the window changes", async () => {
    const user = userEvent.setup();
    renderMetrics();

    expect(screen.getByRole("status", { name: "报告加载中…" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "学习效果与模型成本报告" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "学习效果漏斗" })).toBeVisible();
    expect(screen.getByText("模型调用边界指标（最近 7 天）")).toBeVisible();
    expect(screen.getByText("test-model")).toBeVisible();
    const tabs = screen.getByRole("tablist", { name: "指标与评测" });
    const reportTab = screen.getByRole("tab", { name: "指标报告" });
    const reportPanel = screen.getByRole("tabpanel", { name: "指标报告" });
    expect(tabs.parentElement).toBe(reportPanel.parentElement);
    expect(tabs.nextElementSibling).toBe(reportPanel);
    expect(reportTab).toHaveAttribute("aria-controls", reportPanel.id);

    await user.selectOptions(screen.getByRole("combobox", { name: "模型统计窗口天数" }), "30");
    await waitFor(() => expect(fetch).toHaveBeenLastCalledWith(
      "/api/reports/learning-cost?learnerId=local-demo&days=30",
      { cache: "no-store" },
    ));
  });

  it("user Given a teacher opens advanced model evaluation When selecting the evaluation tab Then all source cases are available without starting a model run", async () => {
    const cases: QualityReviewInboxItem[] = [
      { caseId: "case-math", externalCaseId: "math-page-214", packageId: "pkg-math", ordinal: 1, subject: "math", sourcePage: 214, sourceKind: "verified", sourceFingerprint: "s1", inputFingerprint: "i1", outputFingerprint: "o1", recordVersion: 1, reviewStatus: "human_accepted", sourceVerification: "human_quote_verified", assetVerification: "server_hash_verified", counted: false, sourceKey: "sha256:math", sourceTitle: "Algebra", uploadId: null, chapterIds: ["chapter-math"] },
      { caseId: "case-english", externalCaseId: "english-page-123", packageId: "pkg-english", ordinal: 1, subject: "english", sourcePage: 123, sourceKind: "legacy", sourceFingerprint: "s2", inputFingerprint: "i2", outputFingerprint: "o2", recordVersion: 2, reviewStatus: "needs_changes", sourceVerification: "pending", assetVerification: "pending", counted: false, sourceKey: "package:pkg-english", sourceTitle: "Writing", uploadId: null, chapterIds: [] },
    ];
    const inbox = { items: cases, total: 2, pendingCount: 1, reviewedCount: 1, unlinkedCount: 1, mediaTasks: [], mediaPendingCount: 0 };
    const requests: Array<{ url: string; method: string }> = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, method: init?.method ?? "GET" });
      if (url.startsWith("/api/quality-reviews/inbox")) return new Response(JSON.stringify(inbox), { headers: { "Content-Type": "application/json" } });
      if (url === "/api/quality-review-runs/models") return new Response(JSON.stringify({ providers: [{ provider: "test", label: "测试", available: true, models: ["alpha", "beta"], detail: "" }] }), { headers: { "Content-Type": "application/json" } });
      if (url === "/api/quality-review-runs") return new Response(JSON.stringify({ items: [] }), { headers: { "Content-Type": "application/json" } });
      return new Response(JSON.stringify(report), { headers: { "Content-Type": "application/json" } });
    }));
    const user = userEvent.setup();
    renderMetrics();

    expect(await screen.findByRole("heading", { name: "学习效果与模型成本报告" })).toBeVisible();
    expect(screen.queryByLabelText("基线模型")).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "模型评测" }));

    expect(await screen.findByRole("heading", { name: "模型对比与人工评分" })).toBeVisible();
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", "metrics-tab-evaluation");
    expect(screen.getByText(/math-page-214 · math · PDF 第 214 页 · 版本 1/)).toBeVisible();
    expect(screen.getByText(/english-page-123 · english · PDF 第 123 页 · 版本 2/)).toBeVisible();
    expect(screen.getByRole("button", { name: "启动有界运行" })).toBeDisabled();
    expect(requests.some((request) => request.url === "/api/quality-review-runs" && request.method === "POST")).toBe(false);
  });
});
