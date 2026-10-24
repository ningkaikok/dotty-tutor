import { describe, expect, it } from "vitest";
import { coursewareDocumentStatus } from "./coursewareDocumentStatus";

const readyDocument: Parameters<typeof coursewareDocumentStatus>[0] = {
  status: "draft",
  generationStatus: "completed",
  sourceReadiness: "ready",
  sourceDistributionScope: "classroom",
  automation: null,
  reviewIssues: [],
  pages: [{ generationStatus: "completed", error: null }],
};

describe("courseware document status view", () => {
  it("user Given an older published document without automation metadata When opening management Then shows published and direct-use copy without another publish action", () => {
    const view = coursewareDocumentStatus({
      ...readyDocument,
      status: "published",
      automation: null,
    });

    expect(view).toMatchObject({ kind: "published", label: "已发布", tone: "success", message: "课件已发布，可直接授课。", canPublish: false });
  });

  it("user Given a generation or automatic check still running When opening management Then reports production in progress", () => {
    const view = coursewareDocumentStatus({
      ...readyDocument,
      generationStatus: "running",
      automation: { status: "checking", message: "正在检查来源。" },
    });

    expect(view).toMatchObject({ kind: "making", label: "制作中", message: "正在检查来源。", canPublish: false });
  });

  it("user Given generation needs attention When opening management Then routes to recovery instead of offering publish", () => {
    const view = coursewareDocumentStatus({
      ...readyDocument,
      generationStatus: "partial",
      pages: [{ generationStatus: "failed", error: "公式需要核对" }],
    });

    expect(view).toMatchObject({ kind: "needs-attention", label: "需要处理", tone: "warning", canPublish: false });
    expect(view.message).toContain("打开问题页核对教材来源");
  });

  it("user Given a complete unpublished document When viewing management Then can publish directly while leaving edits optional", () => {
    const view = coursewareDocumentStatus(readyDocument);

    expect(view).toMatchObject({ kind: "unpublished", label: "未发布", canPublish: true });
    expect(view.message).toContain("需要调整时可展开编辑页面");
    expect(view.message).not.toContain("确认");
  });
});
