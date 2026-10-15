// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { CoursewareDraftEditor } from "./CoursewareDraftEditor";
import type { CoursewareAuthoringDocument } from "./coursewareAuthoringView";

afterEach(cleanup);

const draft: CoursewareAuthoringDocument = {
  chapterId: "chapter-1",
  coursewareId: "course-1",
  planVersion: 1,
  audience: "Secondary students",
  languagePolicy: "English with Chinese support",
  sourceRevisionId: "source-1",
  pageStart: 8,
  pageEnd: 9,
  subject: "english",
  title: "Source-based lesson",
  durationMinutes: 40,
  sections: [{ sectionId: "opening", title: "Opening", purpose: "Set the goal", estimatedMinutes: 5, pageIds: ["cover", "write"] }],
  pages: [
    { pageId: "cover", sectionId: "opening", pageKind: "cover", layout: "hero_split", title: "Source-based lesson", objectiveIds: ["objective-1"], sourceRefs: [{ sourceRevisionId: "source-1", page: 8, regionIds: ["region-8"], sentenceIds: ["sentence-8"] }], blocks: [{ kind: "text", blockId: "intro-block", authorship: "source_excerpt", text: "Opening from the selected source.", sourceRefs: [{ sourceRevisionId: "source-1", page: 8, regionIds: ["region-8"], sentenceIds: ["sentence-8"] }] }], teacherNotes: "Keep this teacher-only.", revealSteps: [], estimatedMinutes: 2, generationStatus: "completed", reviewStatus: "pending", version: 1 },
    { pageId: "write", sectionId: "opening", pageKind: "activity", layout: "two_column", title: "Write first", objectiveIds: ["objective-1"], sourceRefs: [{ sourceRevisionId: "source-1", page: 9 }], blocks: [{ kind: "input", blockId: "stable-input-id", prompt: "Write two details", minResponses: 2, placeholder: "One detail" }], teacherNotes: "Pair up after writing.", revealSteps: [], estimatedMinutes: 3, generationStatus: "completed", reviewStatus: "pending", version: 1 },
  ],
  assets: [],
  recordVersion: 3,
  createdAt: 1,
  updatedAt: 1,
  status: "draft",
  generationStatus: "completed",
  sourceReadiness: "ready",
  sourceDistributionScope: "classroom",
  reviewStatus: "pending",
  publicationId: null,
  generationJobId: null,
  reviewIssues: [],
};

function setup(document = draft) {
  const callbacks = {
    onSavePage: vi.fn(async () => undefined),
    onRegeneratePage: vi.fn(async () => undefined),
    onReviewPage: vi.fn(async () => undefined),
    onReviewDocument: vi.fn(async () => undefined),
    onPublish: vi.fn(async () => undefined),
    onReload: vi.fn(async () => undefined),
    onPresent: vi.fn(),
    onLearn: vi.fn(),
  };
  render(<CoursewareDraftEditor document={document} {...callbacks} />);
  return callbacks;
}

it("user Given a failed generated page When selecting its recovery action Then the located page editor opens with only that page available to redo", async () => {
  const broken = { ...draft, generationStatus: "partial" as const, pages: draft.pages.map((page) => page.pageId === "write" ? { ...page, generationStatus: "failed" as const, error: "公式与原页不一致" } : page) };
  setup(broken);
  const recovery = screen.getByRole("region", { name: "制作恢复与下一步" });
  expect(recovery).toHaveTextContent("教材第 9 页");
  await userEvent.click(within(recovery).getByRole("button", { name: "修复 Write first" }));
  const editor = screen.getByRole("region", { name: "编辑页面：Write first" });
  expect(editor).toHaveTextContent("公式与原页不一致");
  await userEvent.click(within(editor).getByRole("button", { name: "重做本页" }));
  expect(within(editor).getByLabelText("重做原因")).toBeVisible();
});

it("user Given unsaved teacher edits When another page needs repair Then page navigation cannot discard those edits", async () => {
  setup();
  await userEvent.type(screen.getByLabelText("页面标题"), " 教师修改");
  expect(screen.getByRole("button", { name: /Write first/ })).toBeDisabled();
  expect(screen.getByLabelText("页面标题")).toHaveValue("Source-based lesson 教师修改");
  expect(screen.getByText("请先保存当前页面修改，再切换页面。" )).toBeVisible();
});

it("user Given a fill-blank activity When enabling and disabling parameter exploration Then saved content keeps the answer and unrelated response rules", async () => {
  const activity = {
    kind: "activity" as const, authorship: "teacher_authored" as const, blockId: "function-block", activityId: "function-example", activityKind: "fill_blank" as const, instructions: "计算 y = x 在 x = 2 时的值",
    assessmentMode: "objective", selectionMode: "single", answerKey: "2", responseSchema: { format: "numeric" },
  } satisfies CoursewareAuthoringDocument["pages"][number]["blocks"][number];
  const callbacks = setup({ ...draft, pages: draft.pages.map((page) => page.pageId === "cover" ? { ...page, blocks: [activity] } : page) });
  await userEvent.click(screen.getByRole("checkbox", { name: "启用函数参数探索" }));
  await userEvent.click(screen.getByRole("button", { name: "保存页面修改" }));
  expect(callbacks.onSavePage).toHaveBeenLastCalledWith("cover", expect.objectContaining({ blocks: [expect.objectContaining({
    instructions: activity.instructions, answerKey: "2",
    responseSchema: { format: "numeric", interaction: "linear_function", slope: 1, intercept: 0, sampleX: 2 },
  })] }));
  await userEvent.click(screen.getByRole("checkbox", { name: "启用函数参数探索" }));
  await userEvent.click(screen.getByRole("button", { name: "保存页面修改" }));
  expect(callbacks.onSavePage).toHaveBeenLastCalledWith("cover", expect.objectContaining({ blocks: [expect.objectContaining({ responseSchema: { format: "numeric" }, answerKey: "2" })] }));
});

it("user Given a generated page with source refs When editing its copy and private notes Then save sends stable block identity and teacher-only content", async () => {
  const user = userEvent.setup();
  const callbacks = setup();
  await user.click(screen.getByRole("button", { name: /Write first/ }));
  const editor = screen.getByRole("region", { name: "编辑页面：Write first" });
  await user.clear(within(editor).getByLabelText("页面标题"));
  await user.type(within(editor).getByLabelText("页面标题"), "Write and compare");
  await user.clear(within(editor).getByLabelText("教师讲稿"));
  await user.type(within(editor).getByLabelText("教师讲稿"), "Discuss in pairs.");
  await user.clear(within(editor).getByLabelText("提示语"));
  await user.type(within(editor).getByLabelText("提示语"), "Write two source details");
  await user.click(within(editor).getByRole("button", { name: "保存页面修改" }));
  expect(callbacks.onSavePage).toHaveBeenCalledWith("write", expect.objectContaining({
    title: "Write and compare",
    teacherNotes: "Discuss in pairs.",
    blocks: [{ kind: "input", blockId: "stable-input-id", prompt: "Write two source details", minResponses: 2, placeholder: "One detail" }],
  }));
});

it("user Given a source excerpt with sentence and region provenance When saving a text edit Then the full evidence locator and block identity stay attached", async () => {
  const user = userEvent.setup();
  const callbacks = setup();
  const editor = screen.getByRole("region", { name: "编辑页面：Source-based lesson" });
  await user.clear(within(editor).getByLabelText("讲解内容"));
  await user.type(within(editor).getByLabelText("讲解内容"), "Edited source explanation.");
  await user.click(within(editor).getByRole("button", { name: "保存页面修改" }));

  expect(callbacks.onSavePage).toHaveBeenCalledWith("cover", expect.objectContaining({
    sourceRefs: [{ sourceRevisionId: "source-1", page: 8, regionIds: ["region-8"], sentenceIds: ["sentence-8"] }],
    blocks: [{ kind: "text", blockId: "intro-block", authorship: "source_excerpt", text: "Edited source explanation.", sourceRefs: [{ sourceRevisionId: "source-1", page: 8, regionIds: ["region-8"], sentenceIds: ["sentence-8"] }] }],
  }));
});

it("user Given an unsaved page When requesting regeneration or review Then the editor requires a reason and keeps publishing gated", async () => {
  const user = userEvent.setup();
  const callbacks = setup();
  const editor = screen.getByRole("region", { name: "编辑页面：Source-based lesson" });
  await user.clear(within(editor).getByLabelText("页面标题"));
  await user.type(within(editor).getByLabelText("页面标题"), "A changed title");
  expect(within(editor).getByRole("button", { name: "重做本页" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "确认并发布教程" })).toBeDisabled();

  await user.click(within(editor).getByRole("button", { name: "保存页面修改" }));
  await user.click(within(editor).getByRole("button", { name: "重做本页" }));
  const reason = within(editor).getByLabelText("重做原因");
  await user.type(reason, "The source quote needs review");
  await user.click(within(editor).getByRole("button", { name: "确认重做这一页" }));
  expect(callbacks.onRegeneratePage).toHaveBeenCalledWith("cover", "The source quote needs review");

  await user.click(within(editor).getByRole("button", { name: "请求修改" }));
  await user.type(within(editor).getByLabelText("审核意见"), "Check the source page");
  await user.click(within(editor).getByRole("button", { name: "提交修改意见" }));
  expect(callbacks.onReviewPage).toHaveBeenCalledWith("cover", "request_changes", "Check the source page");
});

it("user Given a teacher selects exact pages When approving the document Then only those page IDs reach the review boundary", async () => {
  const user = userEvent.setup();
  const callbacks = setup();
  await user.click(screen.getByRole("checkbox", { name: "选择审核 Write first" }));
  await user.click(screen.getByRole("button", { name: "审核所选页" }));
  expect(callbacks.onReviewDocument).toHaveBeenCalledWith("approve", ["write"], "");
});
