// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import type { QualityReviewInbox } from "../../api/qualityReviews";
import { MaterialsApp } from "./MaterialsApp";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("user Given a 773-page textbook with one seven-page published courseware and a draft chapter When opening the library Then the lesson is teachable and the book remains manageable", async () => {
  const book = { uploadId: "book", filename: "Writing Guide.pdf", materialKind: "textbook", questionCount: 0, pageCount: 773, status: "complete" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const path = String(url);
    const body = path.startsWith("/api/quality-reviews/inbox") ? { ...emptyInbox, total: 18, pendingCount: 18 }
      : path === "/api/library" ? { items: [book] }
      : path === "/api/chapters" ? { items: [{ ...course, status: "draft", currentLessonIds: [], uploadId: "book" }] }
      : { items: [{ coursewareId: "lesson", title: "Memoir and evidence", pageCount: 7, status: "published", generationStatus: "completed", publicationId: "publication", recordVersion: 3, automation: null }] };
    return new Response(JSON.stringify(body));
  });
  render(<MemoryRouter><MaterialsApp /></MemoryRouter>);
  expect(await screen.findByRole("link", { name: "直接授课 · Memoir and evidence" })).toHaveAttribute("href", "/teacher/courseware/lesson?publicationId=publication");
  expect(screen.getByRole("link", { name: "管理教材 · Writing Guide.pdf" })).toHaveAttribute("href", "/studio/courseware?uploadId=book");
  expect(screen.getByText("1 份课件 · 7 页 · 1 份可授课")).toBeVisible();
  expect(screen.getByText("已发布", { selector: "span" })).toBeVisible();
  expect(screen.queryByText(/completed|published/)).not.toBeInTheDocument();
  expect(screen.queryByText(/0 节旧版/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "待处理（0）" })).toBeVisible();
  fireEvent.click(screen.getByText("查看教程目录"));
  expect(screen.getByRole("link", { name: "管理章节课件 · A day outdoors" })).toHaveAttribute("href", "/studio/courseware?chapterId=en-1");
  expect(screen.getAllByRole("link", { name: /A day outdoors/ }).filter((link) => link.getAttribute("href") === "/studio/courseware?chapterId=en-1")).toHaveLength(1);
  expect(screen.getByText("A day outdoors", { selector: "strong" })).toBeVisible();
});

it("user Given eighteen optional source evaluation cases When opening tasks Then no production work is required and source checks remain optional", async () => {
  setup(false, { ...emptyInbox, total: 18, pendingCount: 18 }, []);
  fireEvent.click(await screen.findByRole("button", { name: "待处理（0）" }));
  expect(screen.getByText("当前没有需要你处理的制作问题")).toBeVisible();
  fireEvent.click(screen.getByText("来源质量抽检（18 项，可选）"));
  expect(screen.getByText(/用于可选的来源质量抽检，不影响课件生成或使用/)).toBeVisible();
  expect(screen.queryByRole("button", { name: /来源 18/ })).not.toBeInTheDocument();
});

it("user Given courseware listing fails When opening a textbook Then a read error replaces any false zero-courseware claim", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const path = String(url);
    if (path.endsWith("/courseware")) return new Response(JSON.stringify({ detail: "课件读取失败" }), { status: 503 });
    return new Response(JSON.stringify(path.startsWith("/api/quality-reviews/inbox") ? emptyInbox : { items: path === "/api/library" ? [{ ...upload, materialKind: "textbook" }] : [{ ...course, uploadId: upload.uploadId }] }));
  });
  render(<MemoryRouter><MaterialsApp /></MemoryRouter>);
  expect(await screen.findByText("课件列表暂时无法读取，已有课件不会因此丢失。")).toBeVisible();
  expect(screen.queryByText("还没有制作课件")).not.toBeInTheDocument();
});
const course = { chapterId: "en-1", title: "A day outdoors", subject: "english", teachingMode: "tutorial", status: "in_review", version: 1, recordVersion: 2, currentLessonIds: ["lesson-1"], reviewIssues: [], publicationId: null };
const upload = { uploadId: "pdf-1", filename: "数学教材.pdf", pageCount: 12, questionCount: 4, status: "complete" };
const emptyInbox: QualityReviewInbox = { items: [], total: 0, pendingCount: 0, reviewedCount: 0, unlinkedCount: 0, mediaTasks: [], mediaPendingCount: 0 };
function setup(failedUploads = false, inbox: QualityReviewInbox = emptyInbox, courseItems: Array<typeof course & { uploadId?: string }> = [course]) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const path = String(url);
    if (path.endsWith("/courseware")) return new Response(JSON.stringify({ items: [] }), { headers: { "Content-Type": "application/json" } });
    if (path.startsWith("/api/quality-reviews/inbox")) return new Response(JSON.stringify(inbox), { headers: { "Content-Type": "application/json" } });
    return new Response(JSON.stringify({ items: path.endsWith("/api/library") ? [upload] : courseItems }), { status: failedUploads && path.endsWith("/api/library") ? 500 : 200, headers: { "Content-Type": "application/json" } });
  });
  render(<MemoryRouter><MaterialsApp /></MemoryRouter>);
}
it("user Given an uploaded PDF and an English course When opening my materials Then both can be continued from one library", async () => {
  setup();
  expect(await screen.findByRole("heading", { name: "A day outdoors" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "数学教材.pdf" })).toBeVisible();
  expect(screen.getByRole("link", { name: "继续复核 →" })).toHaveAttribute("href", "/studio/chapters/en-1");
  expect(screen.getByRole("link", { name: "管理章节课件 · A day outdoors" })).toHaveAttribute("href", "/studio/courseware?chapterId=en-1");
  expect(screen.getByRole("link", { name: "管理教材 · 数学教材.pdf" })).toHaveAttribute("href", "/studio/courseware?uploadId=pdf-1");
  expect(screen.queryByRole("link", { name: "自动制作课程" })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "查看练习 →" })).toHaveAttribute("href", "/studio/import?uploadId=pdf-1");
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "英语" } });
  expect(screen.queryByRole("heading", { name: "数学教材.pdf" })).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "A day outdoors" })).toBeVisible();
});
it("user Given upload records cannot be fetched When opening the library Then English courses remain available with a specific retry message", async () => {
  setup(true);
  expect(await screen.findByRole("heading", { name: "A day outdoors" })).toBeVisible();
  expect(screen.getAllByRole("alert").some((alert) => alert.textContent?.includes("已上传教材暂时无法读取"))).toBe(true);
  expect(screen.queryByText("从一本教材开始")).not.toBeInTheDocument();
});

it("user Given a textbook and paper When filtering and deleting the paper Then only the confirmed paper disappears", async () => {
  const paper = { ...upload, uploadId: "exam", filename: "中考试卷.pdf", materialKind: "paper" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url, init) => new Response(JSON.stringify(String(url).endsWith("/courseware") ? { items: [] } : String(url).startsWith("/api/quality-reviews/inbox") ? emptyInbox : init?.method === "DELETE" ? { status: "deleted" } : { items: String(url).endsWith("/api/library") ? [{ ...upload, materialKind: "textbook" }, paper] : [course] }), { headers: { "Content-Type": "application/json" } }));
  render(<MemoryRouter><MaterialsApp /></MemoryRouter>);
  await screen.findByRole("heading", { name: paper.filename });
  fireEvent.click(screen.getByRole("button", { name: "试卷" }));
  expect(screen.queryByRole("heading", { name: upload.filename })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "自动制作课程" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: `删除 ${paper.filename}` }));
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(screen.getByRole("heading", { name: paper.filename })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: `删除 ${paper.filename}` }));
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
  await screen.findByRole("heading", { name: "没有找到匹配的材料" });
  fireEvent.click(screen.getByRole("button", { name: "教材与课程" }));
  expect(screen.getByRole("heading", { name: upload.filename })).toBeVisible();
  expect(screen.getByRole("heading", { name: course.title })).toBeVisible();
});
it("user Given course deletion fails When confirming deletion Then the course remains with a retryable error", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url, init) => new Response(JSON.stringify(String(url).endsWith("/courseware") ? { items: [] } : String(url).startsWith("/api/quality-reviews/inbox") ? emptyInbox : init?.method === "DELETE" ? { detail: "暂时无法删除" } : { items: String(url).endsWith("/api/library") ? [upload] : [course] }), { status: init?.method === "DELETE" ? 503 : 200, headers: { "Content-Type": "application/json" } }));
  render(<MemoryRouter><MaterialsApp /></MemoryRouter>);
  await screen.findByRole("heading", { name: course.title });
  fireEvent.click(screen.getByRole("button", { name: `删除 ${course.title}` }));
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
  expect(await screen.findByText("暂时无法删除")).toBeVisible();
  expect(screen.getByRole("heading", { name: course.title })).toBeVisible();
  expect(screen.getByRole("button", { name: "确认删除" })).toBeEnabled();
});

it("user Given one textbook and multiple generated chapters When opening my materials Then one tutorial contains its ordered directory and no independent reading cards", async () => {
  const book = { ...upload, materialKind: "textbook", filename: "English Writing.pdf", questionCount: 0 };
  const contents = [{ ...course, chapterId: "second", title: "Chapter 2 Writing", uploadId: book.uploadId, pageStart: 8, teachingMode: "tutorial" }, { ...course, chapterId: "first", title: "Chapter 1 Writing", uploadId: book.uploadId, pageStart: 2, teachingMode: "tutorial" }];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => new Response(JSON.stringify(String(url).endsWith("/courseware") ? { items: [] } : String(url).startsWith("/api/quality-reviews/inbox") ? emptyInbox : { items: String(url).endsWith("/api/library") ? [book] : contents }), { headers: { "Content-Type": "application/json" } }));
  render(<MemoryRouter><MaterialsApp /></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: book.filename })).toBeVisible();
  expect(screen.getAllByRole("article")).toHaveLength(1);
  expect(screen.queryByText("英语阅读")).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("查看教程目录"));
  expect(screen.getByText("Chapter 1 Writing", { selector: "strong" })).toBeVisible();
  expect(screen.getByRole("link", { name: "管理章节课件 · Chapter 1 Writing" })).toHaveAttribute("href", "/studio/courseware?chapterId=first");
  expect(screen.getAllByRole("listitem")[0]).toHaveTextContent("Chapter 1 Writing");
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Chapter 2" } });
  expect(screen.getByRole("heading", { name: book.filename })).toBeVisible();
});

it("user Given a regenerated tutorial and preserved reading drafts When opening the book Then the tutorial count excludes its old drafts and history remains accessible", async () => {
  const book = { ...upload, materialKind: "textbook", questionCount: 0 };
  const fresh = { ...course, chapterId: "fresh", uploadId: book.uploadId, title: "新的讲解章节", teachingMode: "tutorial" };
  const old = { ...course, uploadId: book.uploadId, title: "原阅读练习", teachingMode: "practice" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => new Response(JSON.stringify(String(url).endsWith("/courseware") ? { items: [] } : String(url).startsWith("/api/quality-reviews/inbox") ? emptyInbox : { items: String(url).endsWith("/api/library") ? [book] : [fresh, old] }), { headers: { "Content-Type": "application/json" } }));
  render(<MemoryRouter><MaterialsApp /></MemoryRouter>);
  await screen.findByRole("heading", { name: book.filename });
  fireEvent.click(screen.getByText("旧版课程内容"));
  expect(screen.getByText("1 章 · 1 节旧版课程内容")).toBeVisible();
  expect(screen.queryByRole("link", { name: "制作讲解教程" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("查看教程目录"));
  expect(screen.getByText(fresh.title, { selector: "strong" })).toBeVisible();
  expect(screen.getByRole("link", { name: old.title })).not.toBeVisible();
  fireEvent.click(screen.getByText("旧版练习草稿（1 章）"));
  expect(screen.getByRole("link", { name: old.title })).toHaveAttribute("href", `/studio/chapters/${old.chapterId}`);
});

it("user Given same-titled legacy packages and published-video tasks When opening my review inbox Then each source stays grouped separately and routes to the correct chapter snapshot", async () => {
  const inbox = {
    items: [
      { caseId: "case-math", externalCaseId: "math-1", packageId: "pkg-math", ordinal: 1, subject: "math", sourcePage: 12, sourceKind: "legacy", sourceFingerprint: "a", inputFingerprint: "b", outputFingerprint: "c", recordVersion: 1, reviewStatus: "pending_human_review", sourceVerification: "pending", assetVerification: "pending", counted: false, sourceKey: "package:pkg-math", sourceTitle: "Legacy Workbook", uploadId: null, chapterIds: [] },
      { caseId: "case-english", externalCaseId: "english-1", packageId: "pkg-english", ordinal: 1, subject: "english", sourcePage: 9, sourceKind: "legacy", sourceFingerprint: "d", inputFingerprint: "e", outputFingerprint: "f", recordVersion: 1, reviewStatus: "needs_changes", sourceVerification: "pending", assetVerification: "pending", counted: false, sourceKey: "package:pkg-english", sourceTitle: "Legacy Workbook", uploadId: null, chapterIds: ["en-1"] },
    ],
    total: 2, pendingCount: 2, reviewedCount: 0, unlinkedCount: 1,
    mediaTasks: [{ mediaId: "media-1", chapterId: "en-1", publicationId: "pub-4", lessonId: "lesson-pub", version: 2, title: "Personal Narrative", status: "consistency_review_required", sourceKey: "sha256:published-file", uploadId: "pdf-1", sourceRevisionId: "revision-old" }],
    mediaPendingCount: 1,
  };
  setup(false, inbox);
  expect(await screen.findByRole("heading", { name: "数学教材.pdf" })).toBeVisible();
  const inboxButton = screen.getByRole("button", { name: "待处理（2）" });
  fireEvent.click(inboxButton);
  fireEvent.click(screen.getByText("来源质量抽检（2 项，可选）"));
  await waitFor(() => expect(screen.getAllByRole("heading", { name: "Legacy Workbook" })).toHaveLength(2));
  expect(screen.getAllByRole("heading", { name: "Legacy Workbook" })).toHaveLength(2);
  expect(screen.getByText("未关联教材的来源包")).toBeVisible();
  expect(screen.getByRole("link", { name: "打开来源复核 math-1" })).toHaveAttribute("href", "/studio/quality-review?packageId=pkg-math&caseId=case-math&returnTo=%2Fstudio");
  expect(screen.getByRole("link", { name: "打开来源复核 english-1" })).toHaveAttribute("href", "/studio/chapters/en-1?sourceReviewPackageId=pkg-english&sourceReviewCaseId=case-english");
  expect(screen.getByRole("heading", { name: "课程待复核（1）" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "教学短视频待审（1）" })).toBeVisible();
  expect(screen.getByText(/视频修订 v2 · 发布快照 pub-4/)).toBeVisible();
  expect(screen.getByRole("link", { name: "打开视频审校" })).toHaveAttribute("href", "/studio/chapters/en-1?mediaReviewPublicationId=pub-4&mediaReviewLessonId=lesson-pub");
  expect(screen.getByRole("combobox", { name: "按章节筛选" })).toBeVisible();
  expect(screen.getByRole("combobox", { name: "按教材或来源筛选" })).toBeVisible();
});

it("user Given the review inbox cannot be read When opening its filter Then materials remain and the count is never shown as zero", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const path = String(url);
    if (path.endsWith("/courseware")) return new Response(JSON.stringify({ items: [] }), { headers: { "Content-Type": "application/json" } });
    if (path.startsWith("/api/quality-reviews/inbox")) return new Response(JSON.stringify({ detail: "服务不可用" }), { status: 503 });
    return new Response(JSON.stringify({ items: path.endsWith("/api/library") ? [upload] : [course] }), { headers: { "Content-Type": "application/json" } });
  });
  render(<MemoryRouter><MaterialsApp /></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: course.title })).toBeVisible();
  expect(screen.getByRole("button", { name: /待处理（数量暂不可用/ })).toBeVisible();
  expect(screen.queryByRole("button", { name: "待处理（0）" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /待处理（数量暂不可用/ }));
  expect(await screen.findByText(/待审列表无法读取/)).toBeVisible();
  expect(screen.getByText(/A day outdoors · 待复核/)).toBeVisible();
});

it("user Given chapter loading fails When opening my materials Then course review counts stay unavailable", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const path = String(url);
    if (path.endsWith("/courseware")) return new Response(JSON.stringify({ items: [] }), { headers: { "Content-Type": "application/json" } });
    if (path.startsWith("/api/quality-reviews/inbox")) return new Response(JSON.stringify(emptyInbox), { headers: { "Content-Type": "application/json" } });
    if (path.endsWith("/api/library")) return new Response(JSON.stringify({ items: [upload] }), { headers: { "Content-Type": "application/json" } });
    return new Response(JSON.stringify({ detail: "章节服务暂不可用" }), { status: 503 });
  });
  render(<MemoryRouter><MaterialsApp /></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: upload.filename })).toBeVisible();
  expect(screen.getByRole("button", { name: /待处理（数量暂不可用/ })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: /待处理（数量暂不可用/ }));
  expect(await screen.findByText("课程暂时无法读取，请重试。")).toBeVisible();
  expect(screen.queryByText(/课程：0 章待复核/)).not.toBeInTheDocument();
});

it("user Given a course draft now cites another textbook while its published video keeps the old source When filtering reviews Then the video stays under its frozen publication source", async () => {
  const currentCase = { caseId: "current-case", externalCaseId: "current-source-1", packageId: "current-package", ordinal: 1, subject: "math", sourcePage: 20, sourceKind: "verified", sourceFingerprint: "s", inputFingerprint: "i", outputFingerprint: "o", recordVersion: 1, reviewStatus: "pending_human_review", sourceVerification: "pending", assetVerification: "server_hash_verified", counted: false, sourceKey: "sha256:current", sourceTitle: "New textbook", uploadId: "pdf-1", chapterIds: ["en-1"] };
  const inbox: QualityReviewInbox = { items: [currentCase], total: 1, pendingCount: 1, reviewedCount: 0, unlinkedCount: 0,
    mediaTasks: [{ mediaId: "old-video", chapterId: "en-1", publicationId: "pub-old", lessonId: "lesson-old", version: 1, title: "Old publication video", status: "consistency_review_required", sourceKey: "sha256:published-old", uploadId: "old-upload", sourceRevisionId: "old-revision" }], mediaPendingCount: 1 };
  setup(false, inbox, [{ ...course, uploadId: "pdf-1" }]);
  fireEvent.click(await screen.findByRole("button", { name: "待处理（2）" }));
  fireEvent.click(screen.getByText(/来源质量抽检（\d+ 项，可选）/));
  const search = screen.getByRole("searchbox");
  fireEvent.change(search, { target: { value: "数学教材.pdf" } });
  expect(screen.getByRole("heading", { name: "课程待复核（1）" })).toBeVisible();
  expect(screen.queryByText(/Old publication video/)).not.toBeInTheDocument();
  fireEvent.change(search, { target: { value: "Old publication video" } });
  expect(screen.getByText(/Old publication video/)).toBeVisible();
  expect(screen.queryByRole("heading", { name: "课程待复核（1）" })).not.toBeInTheDocument();
  fireEvent.change(search, { target: { value: "" } });
  const sourceFilter = screen.getByRole("combobox", { name: "按教材或来源筛选" });
  fireEvent.change(sourceFilter, { target: { value: "sha256:current" } });
  expect(screen.getByRole("heading", { name: "数学教材.pdf" })).toBeVisible();
  expect(screen.queryByText(/Old publication video/)).not.toBeInTheDocument();
  fireEvent.change(sourceFilter, { target: { value: "sha256:published-old" } });
  expect(screen.getByText(/Old publication video/)).toBeVisible();
  expect(screen.getByText(/发布快照 pub-old/)).toBeVisible();
  expect(screen.queryByRole("heading", { name: "数学教材.pdf" })).not.toBeInTheDocument();
});

it("user Given pending courseware drafts When opening the review inbox Then per-page review links appear without claiming teacher approval", async () => {
  setup(false, { ...emptyInbox, coursewarePendingCount: 1, coursewareTasks: [{ coursewareId: "draft-1", chapterId: "en-1", sourceRevisionId: "source-1", sourceKey: "sha256:fixture", uploadId: null, title: "真实教材待审草稿", status: "draft", pageCount: 5, pendingPageCount: 5 }] });
  fireEvent.click(await screen.findByRole("button", { name: "待处理（2）" }));
  fireEvent.click(screen.getByText(/来源质量抽检（\d+ 项，可选）/));
  expect(screen.getByRole("region", { name: "互动课件待审任务" })).toBeVisible();
  expect(screen.getByRole("link", { name: "打开课件逐页复核" })).toHaveAttribute("href", "/studio/courseware/draft-1");
  expect(screen.getByText(/5 页待复核/)).toBeVisible();
});
