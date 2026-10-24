import { expect, it } from "vitest";
import { chapterStatusLabel, coursewareListStatus, coursewareSubjectLabel } from "./coursewareWorkspaceView";

it("user Given a draft chapter with an actual published courseware When showing the lesson Then the courseware publication state remains teachable", () => {
  const view = coursewareListStatus({ status: "published", generationStatus: "completed", publicationId: "publication-7", automation: null });
  expect(view).toEqual({ kind: "published", label: "已发布" });
});

it("user Given automation is checking a draft courseware When showing its state Then the status is in Chinese and actionable", () => {
  const view = coursewareListStatus({ status: "draft", generationStatus: "running", publicationId: null, automation: { status: "checking" } as never });
  expect(view).toEqual({ kind: "making", label: "制作中" });
  expect(view.label).not.toMatch(/running|checking|published|completed/);
});

it("user Given generation failed When rendering state Then the courseware needs attention", () => {
  const view = coursewareListStatus({ status: "draft", generationStatus: "failed", publicationId: null, automation: null });
  expect(view).toEqual({ kind: "needs-attention", label: "需要处理" });
});

it("user Given backend chapter and subject codes When showing the workspace Then visible labels stay in Chinese", () => {
  expect(chapterStatusLabel("draft")).toBe("草稿");
  expect(chapterStatusLabel("unknown_state")).toBe("状态待确认");
  expect(coursewareSubjectLabel("math", "Math")).toBe("数学");
  expect(coursewareSubjectLabel("unknown_subject", "unknown_subject")).toBe("其他学科");
});
