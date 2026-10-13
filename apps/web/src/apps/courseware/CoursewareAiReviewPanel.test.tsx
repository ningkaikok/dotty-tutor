// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { CoursewareAiReviewStatus } from "../../api/aiReview";
import { CoursewareAiReviewPanel } from "./CoursewareAiReviewPanel";

afterEach(() => cleanup());

const status: CoursewareAiReviewStatus = {
  coursewareId: "cw", recordVersion: 4, samplingRate: 0.1, pagesToReview: 1, countsAsHumanReview: false,
  metrics: { compared: 2, agreements: 1, missed: 1, falseAlarms: 0 },
  pages: [
    { pageId: "p1", title: "勾股定理例题", pageVersion: 1, reviewStatus: "pending", route: "needs_review", sampled: false, textOnly: false, verdict: "fail",
      summary: "例题数字与原页不一致", issues: [{ code: "formula_error", severity: "blocking", message: "原页是 3、4、5", sourcePage: 12, quote: "3² + 4² = 5²" }] },
    { pageId: "p2", title: "抽中的通过页", pageVersion: 1, reviewStatus: "pending", route: "needs_review", sampled: true, textOnly: false, verdict: "pass", summary: "未发现问题", issues: [] },
    { pageId: "p3", title: "导入", pageVersion: 1, reviewStatus: "pending", route: "bulk_confirmable", sampled: false, textOnly: false, verdict: "pass", summary: "", issues: [] },
    { pageId: "p4", title: "小结", pageVersion: 1, reviewStatus: "pending", route: "bulk_confirmable", sampled: false, textOnly: false, verdict: "pass", summary: "", issues: [] },
    { pageId: "p5", title: "新改的页", pageVersion: 2, reviewStatus: "pending", route: "stale", sampled: false, textOnly: false, verdict: "pass", summary: "", issues: [] },
  ],
};

it("user Given an AI pre-review When reading it Then flagged and sampled pages are shown with located issues", () => {
  render(<CoursewareAiReviewPanel status={status} error="" starting={false} busy={false} onStart={() => undefined} onBulkConfirm={() => undefined} />);
  const queue = screen.getByRole("region", { name: /需要教师细看/ });
  expect(queue).toHaveTextContent("勾股定理例题");
  expect(queue).toHaveTextContent("阻断 · 公式/数字错误 · 原页 12：原页是 3、4、5");
  expect(queue).toHaveTextContent("抽检");
  expect(screen.getByText(/仅供参考，由教师确认/)).toBeVisible();
  expect(screen.getByText(/AI 漏报 1 页/)).toBeVisible();
  expect(screen.getByText(/新改的页/)).toBeVisible();
});

it("user Given unsampled AI passes When bulk confirming Then only those pages are sent as a teacher review", async () => {
  const confirm = vi.fn();
  render(<CoursewareAiReviewPanel status={status} error="" starting={false} busy={false} onStart={() => undefined} onBulkConfirm={confirm} />);
  await userEvent.click(screen.getByRole("button", { name: "教师批量确认这 2 页" }));
  expect(confirm).toHaveBeenCalledWith(["p3", "p4"]);
});

it("user Given pages awaiting review When starting Then the model call count is shown and a running job cannot be restarted", async () => {
  const start = vi.fn();
  const { rerender } = render(<CoursewareAiReviewPanel status={status} error="" starting={false} busy={false} onStart={start} onBulkConfirm={() => undefined} />);
  await userEvent.click(screen.getByRole("button", { name: "AI 初审 1 页（将调用 1 次审核模型）" }));
  expect(start).toHaveBeenCalledTimes(1);
  rerender(<CoursewareAiReviewPanel status={{ ...status, jobId: "job-1" }} error="" starting={false} busy={false} onStart={start} onBulkConfirm={() => undefined} />);
  expect(screen.getByText(/AI 初审进行中/)).toBeVisible();
  expect(screen.queryByRole("button", { name: /AI 初审 1 页/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "教师批量确认这 2 页" })).toBeDisabled();
});
