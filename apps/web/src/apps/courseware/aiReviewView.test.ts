import { expect, it } from "vitest";
import type { CoursewareAiReviewStatus } from "../../api/aiReview";
import { aiReviewGroups, metricsSummary, samplingLabel } from "./aiReviewView";

const page = (pageId: string, route: CoursewareAiReviewStatus["pages"][number]["route"]) => ({
  pageId, title: pageId, pageVersion: 1, reviewStatus: "pending" as const, route, sampled: false, summary: "", textOnly: false,
});

it("groups pages exactly as the server routed them", () => {
  const status: CoursewareAiReviewStatus = {
    coursewareId: "cw", recordVersion: 3, samplingRate: 0.1, pagesToReview: 1, countsAsHumanReview: false,
    pages: [page("a", "needs_review"), page("b", "bulk_confirmable"), page("c", "stale"), page("d", "not_reviewed"), page("e", "decided")],
    metrics: { compared: 0, agreements: 0, missed: 0, falseAlarms: 0 },
  };
  const groups = aiReviewGroups(status);
  expect(groups.needsReview.map((item) => item.pageId)).toEqual(["a"]);
  expect(groups.bulkConfirmable.map((item) => item.pageId)).toEqual(["b"]);
  expect(groups.unreviewed.map((item) => item.pageId)).toEqual(["c", "d"]);
  expect(groups.decided.map((item) => item.pageId)).toEqual(["e"]);
});

it("claims no reliability before a teacher has checked any AI verdict", () => {
  expect(metricsSummary({ compared: 0, agreements: 0, missed: 0, falseAlarms: 0 })).toContain("暂无一致率");
  expect(metricsSummary({ compared: 4, agreements: 2, missed: 1, falseAlarms: 1 })).toBe("教师已对照 4 页：一致 2 页，AI 漏报 1 页，误报 1 页。");
  expect(samplingLabel(0.1)).toBe("10%");
});
