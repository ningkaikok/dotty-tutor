// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";

const { workflowMock, wholeBookMock } = vi.hoisted(() => ({
  workflowMock: vi.fn(),
  wholeBookMock: vi.fn(),
}));

vi.mock("./useCoursewarePlanWorkflow", () => ({ useCoursewarePlanWorkflow: workflowMock }));
vi.mock("./useWholeBookRun", () => ({ useWholeBookRun: wholeBookMock }));

import { CoursewarePlanWorkspace } from "./CoursewarePlanWorkspace";

afterEach(cleanup);

const published = {
  coursewareId: "courseware-7",
  title: "第一章：证据与写作",
  pageCount: 7,
  status: "published",
  generationStatus: "completed",
  publicationId: "publication-7",
  recordVersion: 16,
  automation: null,
} as const;

function setup(overrides: Record<string, unknown> = {}, wholeBookOverrides: Record<string, unknown> = {}) {
  const startWholeBook = vi.fn(async () => undefined);
  const upload = { uploadId: "book", filename: "Writing Guide.pdf", materialKind: "textbook", pageCount: 773, questionCount: 0, status: "complete" };
  workflowMock.mockReturnValue({
    uploadId: "book", chapterId: "", profiles: [{ subjectId: "english", label: "英语" }], profilesLoading: false, profilesLoaded: true, profilesError: "",
    uploads: [upload], chapters: [{ chapterId: "chapter", uploadId: "book", title: "第一章：证据与写作", subject: "english", teachingMode: "tutorial", status: "draft", pageStart: 1, pageEnd: 7, currentLessonIds: [], recordVersion: 16 }],
    upload, chapter: null, plan: null, documents: [], job: null, jobPurpose: null, preparedChapters: [], loading: false, busy: false,
    error: "", notice: "", audience: "学习者", setAudience: vi.fn(), languagePolicy: "中文", setLanguagePolicy: vi.fn(),
    durationMinutes: 40, setDurationMinutes: vi.fn(), objectives: "学习目标", setObjectives: vi.fn(), pageStart: "", setPageStart: vi.fn(),
    pageEnd: "", setPageEnd: vi.fn(), startSourcePreparation: vi.fn(), startPlan: vi.fn(), savePlan: vi.fn(), approvePlan: vi.fn(),
    chooseUpload: vi.fn(), chooseChapter: vi.fn(), planLoaded: true, selectCompletionRange: vi.fn(), supplementing: false,
    cancelSupplement: vi.fn(), reloadWorkspace: vi.fn(), automaticGeneration: false, retryAutomatic: vi.fn(), retrySource: vi.fn(), retryPlan: vi.fn(),
    uploadDocuments: { chapter: [published] }, uploadDocumentErrors: [], uploadDocumentsLoading: false, uploadError: "",
    documentsLoaded: false, documentsError: "", chaptersError: "", reloadUploadCourseware: vi.fn(), reloadMaterialLists: vi.fn(), ...overrides,
  });
  wholeBookMock.mockReturnValue({ run: {
    runId: "run-1", uploadId: "book", filename: upload.filename, status: "running", message: "正在制作第 6 批。",
    totalPages: 773, completedPages: 150, completedBatches: 5, batchCount: 26, batches: [], canPause: true, canResume: false, createdAt: 1, updatedAt: 2,
  }, loading: false, busy: false, error: "", start: startWholeBook, pause: vi.fn(), resume: vi.fn(), refresh: vi.fn(), ...wholeBookOverrides });
  render(<MemoryRouter initialEntries={["/studio/courseware?uploadId=book"]}><CoursewarePlanWorkspace /></MemoryRouter>);
  return { startWholeBook };
}

function setupChapter(overrides: Record<string, unknown> = {}) {
  const chapter = {
    chapterId: "chapter", uploadId: "book", title: "第一章：证据与写作", subject: "english", teachingMode: "tutorial", status: "draft",
    recordVersion: 16, version: 1, lessons: [], currentLessonIds: [], reviewIssues: [], publications: [], publicationId: null,
    sourceRevisions: [{ sourceRevisionId: "source", uploadId: "book", pageStart: 1, pageEnd: 7, pages: [] }],
  };
  workflowMock.mockReturnValue({
    uploadId: "", chapterId: "chapter", profiles: [{ subjectId: "english", label: "英语" }], profilesLoading: false, profilesLoaded: true, profilesError: "",
    uploads: [], chapters: [], upload: null, chapter, plan: null, documents: [published], documentsLoaded: true, documentsError: "",
    job: null, jobPurpose: null, preparedChapters: [], loading: false, busy: false, error: "", notice: "",
    audience: "学习者", setAudience: vi.fn(), languagePolicy: "中文", setLanguagePolicy: vi.fn(), durationMinutes: 40,
    setDurationMinutes: vi.fn(), objectives: "学习目标", setObjectives: vi.fn(), pageStart: "1", setPageStart: vi.fn(),
    pageEnd: "7", setPageEnd: vi.fn(), startSourcePreparation: vi.fn(), startPlan: vi.fn(), savePlan: vi.fn(), approvePlan: vi.fn(),
    chooseUpload: vi.fn(), chooseChapter: vi.fn(), planLoaded: true, selectCompletionRange: vi.fn(), supplementing: false,
    cancelSupplement: vi.fn(), reloadWorkspace: vi.fn(), automaticGeneration: true, retryAutomatic: vi.fn(), retrySource: vi.fn(), retryPlan: vi.fn(),
    uploadDocuments: {}, uploadDocumentErrors: [], uploadDocumentsLoading: false, uploadError: "", chaptersError: "",
    reloadUploadCourseware: vi.fn(), reloadMaterialLists: vi.fn(), ...overrides,
  });
  wholeBookMock.mockReturnValue({ run: null, loading: false, busy: false, error: "", start: vi.fn(), pause: vi.fn(), resume: vi.fn(), refresh: vi.fn() });
  render(<MemoryRouter initialEntries={["/studio/courseware?chapterId=chapter"]}><CoursewarePlanWorkspace /></MemoryRouter>);
}

it("user Given a 773-page book with one published seven-page courseware and a draft chapter When opening book management Then actual courseware and progress appear before optional setup", () => {
  const { startWholeBook } = setup();
  const headings = screen.getAllByRole("heading", { level: 1 });
  expect(headings).toHaveLength(1);
  expect(headings[0]).toHaveTextContent("Writing Guide.pdf");
  expect(screen.getByText(/773 页 · 查看已有关联章节/)).toBeVisible();
  expect(screen.getByRole("link", { name: "直接授课" })).toHaveAttribute("href", "/teacher/courseware/courseware-7?publicationId=publication-7");
  expect(screen.getByRole("link", { name: "管理章节课件 · 第一章：证据与写作" })).toHaveAttribute("href", "/studio/courseware?chapterId=chapter");
  expect(screen.getByText("已发布", { selector: "span" })).toBeVisible();
  expect(screen.queryByText(/draft|published|completed/)).not.toBeInTheDocument();
  const progress = screen.getByRole("region", { name: "整本自动制作进度" });
  const chapterList = screen.getByRole("region", { name: "教材章节与课件" });
  expect(progress).toBeVisible();
  expect(progress.compareDocumentPosition(chapterList) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  const newProduction = screen.getByText(/^开始新制作/).closest("details");
  expect(newProduction).not.toHaveAttribute("open");
  expect(screen.getByText("选择范围后开始，完成后可直接授课。")).not.toBeVisible();
  expect(startWholeBook).not.toHaveBeenCalled();
});

it("user Given an existing published lesson When opening new-production settings Then whole-book is recommended but no task starts until the explicit button", async () => {
  const user = userEvent.setup();
  const { startWholeBook } = setup();
  await user.click(screen.getByText(/^开始新制作/));

  expect(screen.getByRole("radio", { name: "整本自动续做（773 页）" })).toBeChecked();
  expect(screen.queryByRole("heading", { name: "选择范围，一次生成教程" })).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Writing Guide.pdf");
  expect(startWholeBook).not.toHaveBeenCalled();
});

it("user Given a book with no courseware and one related chapter in a configured subject When opening management Then setup is ready without an extra click and reuses that subject", () => {
  setup({ uploadDocuments: { chapter: [] } }, { run: null });

  expect(screen.getByLabelText("学科")).toHaveValue("english");
  expect(screen.getByRole("radio", { name: "整本自动续做（773 页）" })).toBeChecked();
  expect(screen.getByText(/选择范围后开始，完成后可直接授课。/)).toBeVisible();
  expect(screen.queryByText(/^开始新制作/)).not.toBeInTheDocument();
});

it("user Given mixed related chapter subjects When opening a book with no courseware Then the teacher chooses the subject", () => {
  setup({
    profiles: [{ subjectId: "english", label: "英语" }, { subjectId: "math", label: "数学" }],
    chapters: [
      { chapterId: "chapter", uploadId: "book", title: "第一章", subject: "english", teachingMode: "tutorial", status: "draft", pageStart: 1, pageEnd: 7 },
      { chapterId: "chapter-2", uploadId: "book", title: "第二章", subject: "math", teachingMode: "tutorial", status: "draft", pageStart: 8, pageEnd: 15 },
    ],
    uploadDocuments: { chapter: [], "chapter-2": [] },
  }, { run: null });

  expect(screen.getByLabelText("学科")).toHaveValue("");
});

it("user Given a book with no related tutorial chapter When opening new production Then the teacher chooses a subject instead of inferring it from the filename", () => {
  setup({ profiles: [{ subjectId: "english", label: "英语" }, { subjectId: "math", label: "数学" }], chapters: [], uploadDocuments: {} }, { run: null });

  expect(screen.getByLabelText("学科")).toHaveValue("");
});

it("user Given a subject configuration request failed while opening a chapter When the page settles Then the error and retry are visible without claiming the subject is unconfigured", async () => {
  const user = userEvent.setup();
  const retry = vi.fn();
  setupChapter({ chapter: null, profilesLoaded: false, profilesLoading: false, profilesError: "学科配置服务暂时不可用。", error: "学科配置服务暂时不可用。", reloadMaterialLists: retry });

  expect(screen.getByRole("heading", { name: "学科配置暂时无法读取" })).toBeVisible();
  expect(screen.getByRole("alert")).toHaveTextContent("学科配置服务暂时不可用。");
  expect(screen.queryByText("学科尚未配置")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "重新读取学科配置" }));
  expect(retry).toHaveBeenCalledTimes(1);
});

it("user Given the courseware list cannot be read When opening book management Then the error is not shown as an empty list or a rebuild prompt", () => {
  setup({ uploadDocumentErrors: ["chapter"], uploadDocuments: { chapter: [] } });
  expect(screen.getByText("部分课件暂时无法显示。请重新读取，或返回教材库。")).toBeVisible();
  expect(screen.queryByText("还没有制作课件")).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "自动制作课件 →" })).not.toBeInTheDocument();
  expect(screen.queryByText(/^开始新制作/)).not.toBeInTheDocument();
});

it("user Given a published courseware under a draft chapter When opening chapter management Then the published courseware is primary and no stale automatic-generation prompt appears", () => {
  setupChapter();
  expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("第一章：证据与写作");
  expect(screen.getByRole("link", { name: "直接授课" })).toHaveAttribute("href", "/teacher/courseware/courseware-7?publicationId=publication-7");
  expect(screen.getByText("已发布", { selector: "span" })).toBeVisible();
  expect(screen.queryByText(/draft|published|completed|STEP 2|自动制作教程/)).not.toBeInTheDocument();
  expect(screen.getByText("继续制作新课件或补齐教材页")).toBeVisible();
  expect(screen.getByText("继续制作新课件或补齐教材页").closest("details")).not.toHaveAttribute("open");
});

it("user Given the chapter courseware list fails to load When opening chapter management Then the page does not show an empty list or new-production form", () => {
  setupChapter({ documents: [], documentsLoaded: false, documentsError: "暂时不可用", automaticGeneration: false });
  expect(screen.getByText("课件暂时无法显示，请重新读取，或返回教材库。")).toBeVisible();
  expect(screen.queryByText("目前没有课件。选择“制作新课件”后可使用已准备的教材来源。")).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "制作新课件" })).not.toBeInTheDocument();
});
