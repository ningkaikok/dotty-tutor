// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { CoursewareCoveragePanel } from "./CoursewareCoveragePanel";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const coverage = {
  chapterId: "chapter-1", sourceRevisionId: "source-1", pageStart: 1, pageEnd: 12,
  complete: false, missingSourcePages: [9], unplannedPages: [4, 5, 6, 7, 8, 10, 11, 12],
  ungeneratedPages: [4, 5, 6, 7, 8, 9, 10, 11, 12], unreviewedPages: [2, 3], unpublishedPages: [2, 3],
};
const completion = {
  chapterId: "chapter-1", sourceRevisionId: "source-1", expectedRecordVersion: 1, maxPagesPerBatch: 5,
  alreadyPlannedPages: [], pendingGenerationPages: [], existingPlan: null, canCreatePlan: true, action: "create_plan", requiresHumanReview: true,
  batches: [{ pageStart: 4, pageEnd: 8, sourcePages: [4, 5, 6, 7, 8] }, { pageStart: 10, pageEnd: 12, sourcePages: [10, 11, 12] }],
  blockedPages: [9], alreadyGeneratedPages: [1, 2, 3], pendingReviewPages: [2, 3],
};
function setup(report: Record<string, unknown> = completion, completionStatus = 200) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.includes("courseware-completion")) return new Response(JSON.stringify(completionStatus === 200 ? report : { detail: "补齐建议读取失败" }), { status: completionStatus });
    return new Response(JSON.stringify(coverage));
  });
}
it("user Given an unplanned unit with a missing source page When selecting a bounded gap Then only its safe range is offered to the existing planning form", async () => {
  setup();
  const ranges: Array<{ pageStart: number; pageEnd: number; supplement: boolean }> = [];
  render(<CoursewareCoveragePanel chapterId="chapter-1" sourceRevisionId="source-1" onSelectRange={(range) => ranges.push(range)} />);
  await userEvent.click(await screen.findByRole("button", { name: "规划教材原页 4–8" }));
  expect(ranges).toEqual([{ pageStart: 4, pageEnd: 8, supplement: false }]);
  expect(screen.getByText(/原页 9.*来源/)).toBeVisible();
  expect(screen.queryByRole("button", { name: /规划教材原页 9/ })).not.toBeInTheDocument();
});
it("user Given an existing approved plan When reading completion gaps Then no replacement action is offered and generated work is kept separate", async () => {
  setup();
  render(<CoursewareCoveragePanel chapterId="chapter-1" sourceRevisionId="source-1" planStatus="approved" onSelectRange={() => undefined} />);
  expect(await screen.findByRole("heading", { name: "补齐完整单元" })).toBeVisible();
  expect(screen.queryByRole("button", { name: /规划教材原页/ })).not.toBeInTheDocument();
  expect(screen.getByText(/已确认规划.*不会覆盖/)).toBeVisible();
  expect(screen.getByText(/已生成.*原页 1–3/)).toBeVisible();
  expect(screen.getByText(/待教师复核.*原页 2–3/)).toBeVisible();
});
it("user Given an outdated source report When reading completion suggestions Then actionable ranges fail closed", async () => {
  setup({ ...completion, sourceRevisionId: "old-source" });
  render(<CoursewareCoveragePanel chapterId="chapter-1" sourceRevisionId="source-1" onSelectRange={() => undefined} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("来源版本已变化");
  expect(screen.queryByRole("button", { name: /规划教材原页/ })).not.toBeInTheDocument();
});
it("user Given a failed suggestion request When the coverage report still loads Then coverage remains visible and retry is available", async () => {
  setup(completion, 500);
  render(<CoursewareCoveragePanel chapterId="chapter-1" sourceRevisionId="source-1" />);
  expect(await screen.findByRole("heading", { name: "完整单元覆盖" })).toBeVisible();
  expect(await screen.findByRole("alert")).toHaveTextContent("补齐建议读取失败");
  expect(screen.getByRole("button", { name: "重试补齐建议" })).toBeEnabled();
});

it("user Given a partially reviewed unit When opening coverage Then missing original pages remain visible without claiming quality approval", async () => {
  setup();
  render(<CoursewareCoveragePanel chapterId="chapter-1" sourceRevisionId="source-1" />);
  expect(await screen.findByRole("heading", { name: "完整单元覆盖" })).toBeVisible();
  expect(screen.getByRole("region", { name: "完整单元覆盖" })).toHaveTextContent("仍有缺口");
  expect(screen.getByRole("region", { name: "完整单元覆盖" })).toHaveTextContent("原页 2–3");
  expect(screen.getByRole("region", { name: "完整单元覆盖" })).toHaveTextContent("教学质量仍需教师验收");
});
it("user Given a coverage request failure When opening the unit Then the failure is not displayed as complete", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ detail: "Source missing" }), { status: 409 }));
  render(<CoursewareCoveragePanel chapterId="chapter-1" sourceRevisionId="source-1" />);
  expect(await screen.findByText(/完整单元覆盖暂时无法读取/)).toBeVisible();
  expect(screen.queryByText("结构覆盖完整")).not.toBeInTheDocument();
});
it("user Given a finished approved plan When the server offers a supplement Then the gap is completed without replacing confirmed lessons", async () => {
  const existingPlan = { sourceRevisionId: "source-1", planVersion: 2, recordVersion: 4, status: "approved", generationStatus: "completed", pageStart: 1, pageEnd: 3, stale: false };
  setup({ ...completion, existingPlan, supplementsExistingPlan: true });
  const ranges: Array<{ pageStart: number; pageEnd: number; supplement: boolean }> = [];
  render(<CoursewareCoveragePanel chapterId="chapter-1" sourceRevisionId="source-1" expectedRecordVersion={1} planStatus="approved" onSelectRange={(range) => ranges.push(range)} />);
  await userEvent.click(await screen.findByRole("button", { name: "补齐教材原页 10–12" }));
  expect(ranges).toEqual([{ pageStart: 10, pageEnd: 12, supplement: true }]);
  expect(screen.getByText(/已确认的课时会保留/)).toBeVisible();
  expect(screen.queryByRole("button", { name: /规划教材原页/ })).not.toBeInTheDocument();
});
