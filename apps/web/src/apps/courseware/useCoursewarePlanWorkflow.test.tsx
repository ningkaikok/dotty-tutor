// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiRequestError } from "../../api/client";

const { api, pending } = vi.hoisted(() => ({
  pending: new Map<string, (value: unknown) => void>(),
  api: {
    listCoursewareProfiles: vi.fn(),
    loadLibrary: vi.fn(),
    listChapters: vi.fn(),
    loadChapter: vi.fn(),
    loadCoursewarePlan: vi.fn(),
    listChapterCourseware: vi.fn(),
    loadBackgroundJob: vi.fn(),
    retryBackgroundJob: vi.fn(),
    prepareCoursewareSource: vi.fn(),
    createCoursewarePlan: vi.fn(),
    editCoursewarePlan: vi.fn(),
    approveCoursewarePlan: vi.fn(),
  },
}));

vi.mock("../../api/courseware", () => api);
vi.mock("../../api/chapters", () => ({
  listChapters: api.listChapters,
  loadChapter: api.loadChapter,
}));
vi.mock("../../api/textbooks", () => ({
  loadLibrary: api.loadLibrary,
  loadBackgroundJob: api.loadBackgroundJob,
  retryBackgroundJob: api.retryBackgroundJob,
}));

import { useCoursewarePlanWorkflow } from "./useCoursewarePlanWorkflow";

function chapter(chapterId: string) {
  return {
    chapterId,
    uploadId: "book",
    title: `章节 ${chapterId}`,
    subject: "math",
    teachingMode: "tutorial",
    status: "draft",
    recordVersion: 1,
    sourceRevisions: [{ sourceRevisionId: `source-${chapterId}`, uploadId: "book", pageStart: 1, pageEnd: 7 }],
  };
}

function WorkflowReader() {
  const workflow = useCoursewarePlanWorkflow();
  return <>
    <button type="button" onClick={() => workflow.chooseChapter("chapter-b")}>切换到章节 B</button>
    <output data-testid="chapter">{workflow.chapter?.chapterId ?? "尚未加载"}</output>
    <output data-testid="document">{workflow.documents[0]?.title ?? "没有课件"}</output>
    <output data-testid="profiles-loading">{String(workflow.profilesLoading)}</output>
    <output data-testid="profiles-error">{workflow.profilesError}</output>
  </>;
}

beforeEach(() => {
  pending.clear();
  api.listCoursewareProfiles.mockReset().mockResolvedValue({ items: [{ subjectId: "math", label: "数学" }] });
  api.loadLibrary.mockReset().mockResolvedValue([]);
  api.listChapters.mockReset().mockResolvedValue([]);
  api.loadChapter.mockReset().mockImplementation((id: string) => new Promise((resolve) => pending.set(id, resolve)));
  api.loadCoursewarePlan.mockReset().mockRejectedValue(new ApiRequestError("没有规划", 404));
  api.listChapterCourseware.mockReset().mockImplementation(async (id: string) => ({ items: [{ coursewareId: `doc-${id}`, title: `课件 ${id}`, pageCount: 7, status: "draft", generationStatus: "completed", recordVersion: 1 }] }));
  api.loadBackgroundJob.mockReset();
  api.retryBackgroundJob.mockReset();
  api.prepareCoursewareSource.mockReset();
  api.createCoursewarePlan.mockReset();
  api.editCoursewarePlan.mockReset();
  api.approveCoursewarePlan.mockReset();
});

afterEach(cleanup);

it("user Given the subject configuration request fails When chapter data is still pending Then configuration failure is separate from pending and is available to retry", async () => {
  api.listCoursewareProfiles.mockRejectedValueOnce(new Error("学科服务暂时不可用"));
  render(<MemoryRouter initialEntries={["/studio/courseware?chapterId=chapter-a"]}><WorkflowReader /></MemoryRouter>);

  await waitFor(() => expect(screen.getByTestId("profiles-error")).toHaveTextContent("学科服务暂时不可用"));
  expect(screen.getByTestId("profiles-loading")).toHaveTextContent("false");
  expect(screen.getByTestId("chapter")).toHaveTextContent("尚未加载");
});

it("user Given chapter A is still loading When the teacher switches to chapter B and A returns late Then B's plan and courseware stay visible", async () => {
  render(<MemoryRouter initialEntries={["/studio/courseware?chapterId=chapter-a"]}><WorkflowReader /></MemoryRouter>);
  await waitFor(() => expect(pending.has("chapter-a")).toBe(true));

  fireEvent.click(screen.getByRole("button", { name: "切换到章节 B" }));
  await waitFor(() => expect(pending.has("chapter-b")).toBe(true));
  await act(async () => { pending.get("chapter-b")?.(chapter("chapter-b")); });
  await waitFor(() => expect(screen.getByTestId("document")).toHaveTextContent("课件 chapter-b"));

  await act(async () => { pending.get("chapter-a")?.(chapter("chapter-a")); });
  expect(screen.getByTestId("chapter")).toHaveTextContent("chapter-b");
  expect(screen.getByTestId("document")).toHaveTextContent("课件 chapter-b");
});
