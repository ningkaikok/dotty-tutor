// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { QualityReviewRun } from "../../api/qualityReviewRuns";
import type { QualityReviewCaseSummary } from "../../api/qualityReviews";
import { QualityReviewRunsPanel } from "./QualityReviewRunsPanel";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const summary: QualityReviewCaseSummary = {
  caseId: "case-1",
  externalCaseId: "math-p214-example-2-2",
  packageId: "package-1",
  ordinal: 1,
  subject: "math",
  sourcePage: 214,
  sourceKind: "real_open_material",
  sourceFingerprint: "s".repeat(64),
  inputFingerprint: "i".repeat(64),
  outputFingerprint: "o".repeat(64),
  recordVersion: 3,
  reviewStatus: "pending_human_review",
  sourceVerification: "managed_asset_verified",
  assetVerification: "server_hash_verified",
  counted: false,
};

const runFixture = (): QualityReviewRun => ({
  runId: "run-1234567890",
  taskInstruction: "核对问题并引用关键步骤。",
  status: "completed",
  cancelRequested: false,
  perCallTimeoutSeconds: 30,
  totalBudgetSeconds: 240,
  maxOutputTokens: 500,
  error: null,
  cases: [{
    caseId: "case-1",
    ordinal: 1,
    recordVersion: 3,
    sourceKind: "real_open_material",
    sourceFingerprint: "s".repeat(64),
    inputFingerprint: "i".repeat(64),
    outputFingerprint: "o".repeat(64),
    snapshot: {
      source: { sourceTitle: "Elementary Algebra 2e" },
      sourceLocator: { quote: "Why do we subtract 37 from both sides?" },
      sourcePage: 214,
      input: { question: "Why do we subtract 37 from both sides?", passage: "We want to isolate the term containing x." },
      expected: { answer: "Subtracting the same value preserves equality." },
      actual: { answer: "Subtract 37 from both sides." },
    },
    arms: {
      A: {
        arm: "A", status: "succeeded", output: { text: "Subtract 37 from both sides to isolate x." },
        durationMs: 1200, promptTokens: 80, outputTokens: 22, cost: null, error: null,
      },
      B: {
        arm: "B", status: "succeeded", output: { text: "Keep equality by doing the same operation." },
        durationMs: 1400, promptTokens: 82, outputTokens: 21, cost: null, error: null,
      },
    },
  }],
  ratings: [],
  blind: true,
  models: null,
});

const runningRunFixture = (): QualityReviewRun => ({ ...runFixture(), status: "running" });

const ratedRunFixture = (): QualityReviewRun => {
  const run = runningRunFixture();
  return {
    ...run,
    status: "completed",
    blind: false,
    models: {
      baseline: { provider: "codex", model: "gpt-review-a" },
      candidate: { provider: "deepseek", model: "deepseek-review-b" },
    },
    cases: run.cases.map((item) => ({
      ...item,
      arms: {
        A: { ...item.arms.A!, actualProvider: "codex", actualModel: "gpt-review-a" },
        B: { ...item.arms.B!, actualProvider: "deepseek", actualModel: "deepseek-review-b" },
      },
    })),
    ratings: [{
      caseId: "case-1", actorId: "demo:unverified", actorKind: "demo_unverified", identityVerified: false,
      aPass: true, bPass: true, preferred: "tie", rationale: "Both explain the equality step.",
    }],
  };
};

function response(payload: unknown) {
  return new Response(JSON.stringify(payload), { headers: { "Content-Type": "application/json" } });
}

it("user Given a completed blind run When rating and reopening it Then the frozen case, revealed models and saved rating remain visible", async () => {
  let persistedRun: QualityReviewRun | null = null;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/api/quality-review-runs/models")) return response({ providers: [
      { provider: "codex", label: "Codex", available: true, models: ["gpt-review-a"], detail: "ready" },
      { provider: "deepseek", label: "DeepSeek", available: true, models: ["deepseek-review-b"], detail: "ready" },
    ] });
    if (url.endsWith("/api/quality-review-runs") && init?.method === "POST") {
      persistedRun = runFixture();
      return response(persistedRun);
    }
    if (url.endsWith("/api/quality-review-runs") && !init?.method) {
      return response({ items: persistedRun ? [persistedRun] : [] });
    }
    if (url.endsWith("/ratings") && init?.method === "POST" && persistedRun) {
      const submitted = JSON.parse(String(init.body)) as { caseId: string; aPass: boolean; bPass: boolean; preferred: "A" | "B" | "tie" | "neither"; rationale: string };
      persistedRun = {
        ...persistedRun,
        blind: false,
        models: {
          baseline: { provider: "codex", model: "gpt-review-a" },
          candidate: { provider: "deepseek", model: "deepseek-review-b" },
        },
        cases: persistedRun.cases.map((item) => ({
          ...item,
          arms: {
            A: { ...item.arms.A!, actualProvider: "codex", actualModel: "gpt-review-a" },
            B: { ...item.arms.B!, actualProvider: "deepseek", actualModel: "deepseek-review-b" },
          },
        })),
        ratings: [{
          caseId: submitted.caseId, actorId: "demo:unverified", actorKind: "demo_unverified", identityVerified: false,
          aPass: submitted.aPass, bPass: submitted.bPass, preferred: submitted.preferred, rationale: submitted.rationale,
        }],
      };
      return response(persistedRun);
    }
    throw new Error(`Unexpected API request: ${url}`);
  });

  const firstMount = render(<QualityReviewRunsPanel cases={[summary]} />);
  expect(await screen.findByText("math-p214-example-2-2 · math · PDF 第 214 页 · 版本 3")).toBeVisible();
  fireEvent.click(screen.getByLabelText(/math-p214-example-2-2/));
  await waitFor(() => expect(screen.getByRole("button", { name: "启动有界运行" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "启动有界运行" }));

  expect(await screen.findByText("核对问题并引用关键步骤。", { exact: false })).toBeVisible();
  const frozenCase = within(screen.getByRole("region", { name: "冻结案例内容" }));
  expect(frozenCase.getByRole("heading", { name: "供模型处理的案例" })).toBeVisible();
  expect(frozenCase.getByText("We want to isolate the term containing x.")).toBeVisible();
  expect(frozenCase.getByText(/Subtracting the same value preserves equality/)).toBeVisible();
  expect(screen.queryByText(/揭盲模型：/)).not.toBeInTheDocument();
  expect(screen.queryByText(/揭盲：基线/)).not.toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("评分理由"), { target: { value: "A correctly explains the equality-preserving operation." } });
  fireEvent.click(screen.getByRole("button", { name: "保存评分" }));
  expect(await screen.findByText(/揭盲：基线 codex\/gpt-review-a/)).toBeVisible();
  expect(screen.getByText(/揭盲模型：codex\/gpt-review-a/)).toBeVisible();
  expect(screen.getByText(/已保存你的评分：A 通过 · B 通过/)).toBeVisible();

  firstMount.unmount();
  render(<QualityReviewRunsPanel cases={[summary]} />);
  expect(await screen.findByText(/揭盲：基线 codex\/gpt-review-a/)).toBeVisible();
  expect(screen.getByText(/已保存你的评分：A 通过 · B 通过/)).toBeVisible();
  expect(screen.getByText(/揭盲模型：deepseek\/deepseek-review-b/)).toBeVisible();
});

it("user Given a status poll is still pending When cancelling a run Then its late running response cannot undo the cancellation", async () => {
  let releasePoll: ((value: Response) => void) | undefined;
  const running = runningRunFixture();
  const providers = { providers: [
    { provider: "codex", label: "Codex", available: true, models: ["gpt-review-a"], detail: "ready" },
    { provider: "deepseek", label: "DeepSeek", available: true, models: ["deepseek-review-b"], detail: "ready" },
  ] };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/api/quality-review-runs/models")) return response(providers);
    if (url.endsWith("/api/quality-review-runs") && init?.method === "POST") return response(running);
    if (url.endsWith("/api/quality-review-runs") && !init?.method) return response({ items: [] });
    if (url.endsWith("/cancel") && init?.method === "POST") {
      return response({ accepted: true, run: { ...running, status: "cancelled", cancelRequested: true } });
    }
    if (url.endsWith(`/api/quality-review-runs/${running.runId}`)) {
      return new Promise<Response>((resolve) => { releasePoll = resolve; });
    }
    throw new Error(`Unexpected API request: ${url}`);
  });

  render(<QualityReviewRunsPanel cases={[summary]} />);
  await screen.findByText("math-p214-example-2-2 · math · PDF 第 214 页 · 版本 3");
  fireEvent.click(screen.getByLabelText(/math-p214-example-2-2/));
  await waitFor(() => expect(screen.getByRole("button", { name: "启动有界运行" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "启动有界运行" }));
  expect(await screen.findByRole("heading", { name: "运行 执行中" })).toBeVisible();
  await waitFor(() => expect(releasePoll).toBeDefined(), { timeout: 2500 });

  fireEvent.click(screen.getByRole("button", { name: "取消运行" }));
  expect(await screen.findByRole("heading", { name: "运行 已取消" })).toBeVisible();
  await act(async () => {
    releasePoll?.(response(running));
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(screen.getByRole("heading", { name: "运行 已取消" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "取消运行" })).not.toBeInTheDocument();
});

it("user Given a status poll is pending When saving a human rating Then its late blind response cannot hide the reveal", async () => {
  let releasePoll: ((value: Response) => void) | undefined;
  const running = runningRunFixture();
  const rated = ratedRunFixture();
  const providers = { providers: [
    { provider: "codex", label: "Codex", available: true, models: ["gpt-review-a"], detail: "ready" },
    { provider: "deepseek", label: "DeepSeek", available: true, models: ["deepseek-review-b"], detail: "ready" },
  ] };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/api/quality-review-runs/models")) return response(providers);
    if (url.endsWith("/api/quality-review-runs") && init?.method === "POST") return response(running);
    if (url.endsWith("/api/quality-review-runs") && !init?.method) return response({ items: [] });
    if (url.endsWith("/ratings") && init?.method === "POST") return response(rated);
    if (url.endsWith(`/api/quality-review-runs/${running.runId}`)) {
      return new Promise<Response>((resolve) => { releasePoll = resolve; });
    }
    throw new Error(`Unexpected API request: ${url}`);
  });

  render(<QualityReviewRunsPanel cases={[summary]} />);
  await screen.findByText("math-p214-example-2-2 · math · PDF 第 214 页 · 版本 3");
  fireEvent.click(screen.getByLabelText(/math-p214-example-2-2/));
  await waitFor(() => expect(screen.getByRole("button", { name: "启动有界运行" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "启动有界运行" }));
  await screen.findByRole("heading", { name: "运行 执行中" });
  await waitFor(() => expect(releasePoll).toBeDefined(), { timeout: 2500 });

  fireEvent.change(screen.getByLabelText("评分理由"), { target: { value: "Both explain the equality step." } });
  fireEvent.click(screen.getByRole("button", { name: "保存评分" }));
  expect(await screen.findByText(/揭盲：基线 codex\/gpt-review-a/)).toBeVisible();
  await act(async () => {
    releasePoll?.(response(running));
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(screen.getByText(/揭盲：基线 codex\/gpt-review-a/)).toBeVisible();
  expect(screen.getByText(/已保存你的评分：A 通过 · B 通过/)).toBeVisible();
});
