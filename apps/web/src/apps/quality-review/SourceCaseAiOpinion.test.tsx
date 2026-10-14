// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { SourceCaseAiOpinion } from "./SourceCaseAiOpinion";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const empty = { caseId: "case-1", caseVersion: 2, verdict: null, summary: "", issues: [], stale: false, textOnly: false, model: null, reviewedAt: null, jobId: null, countsAsHumanReview: false };
const answered = { ...empty, verdict: "concern", summary: "输出漏了一个条件", issues: [{ code: "missing_condition", severity: "minor", message: "缺少 x>0", sourcePage: 214, quote: "x > 0" }] };

it("user Given a source case When asking for an AI opinion Then it is shown as advisory and never as the decision", async () => {
  const requests: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    requests.push(`${init?.method ?? "GET"} ${String(input)}`);
    return new Response(JSON.stringify(init?.method === "POST" ? answered : empty));
  });
  render(<SourceCaseAiOpinion caseId="case-1" recordVersion={2} />);
  await userEvent.click(await screen.findByRole("button", { name: "请求 AI 参考意见（调用 1 次审核模型）" }));
  expect(await screen.findByText(/输出漏了一个条件/)).toBeVisible();
  expect(screen.getByText(/遗漏条件 · 原页 214：缺少 x>0/)).toBeVisible();
  expect(screen.getByText("不是人工决定，不计入金标准")).toBeVisible();
  expect(screen.getByText(/最终决定仍由你提交/)).toBeVisible();
  expect(requests).toContain("POST /api/quality-review/cases/case-1/ai-opinion");
});

it("user Given an opinion on an older case version When the case was revised Then a fresh request is offered", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ ...answered, stale: true })));
  render(<SourceCaseAiOpinion caseId="case-1" recordVersion={3} />);
  expect(await screen.findByRole("button", { name: /案例已修订，重新请求 AI 参考意见/ })).toBeEnabled();
  expect(screen.queryByText(/输出漏了一个条件/)).not.toBeInTheDocument();
});
