// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MediaStudioPanel } from "./MediaStudioPanel";
import {
  createLessonMediaDraft,
  listLessonMedia,
  publishLessonMedia,
  reviewLessonMedia,
  type LessonMedia,
} from "../../api/lessonMedia";

vi.mock("../../api/lessonMedia", () => ({
  cancelLessonMediaRender: vi.fn(),
  createLessonMediaDraft: vi.fn(),
  createLessonMediaRevision: vi.fn(),
  enqueueLessonMediaRender: vi.fn(),
  lessonMediaTeacherAssetUrl: (id: string, kind: string) => `/api/lesson-media/${id}/${kind}`,
  listLessonMedia: vi.fn(),
  publishLessonMedia: vi.fn(),
  reviewLessonMedia: vi.fn(),
}));

const lessons = [{ lessonId: "lesson-1", title: "解一元一次方程", sourceRevisionId: "source-4" }];
const draft: LessonMedia = {
  mediaId: "media-5", seriesId: "series-1", version: 1, chapterId: "chapter-2",
  publicationId: "publication-3", lessonId: "lesson-1", sourceRevisionId: "source-4",
  title: "解一元一次方程", subject: "math", license: "CC BY 4.0", sourceTitle: "教材",
  sourceUrl: "https://openstax.org/details/books/example", attributionText: "Access for free at openstax.org.",
  distributionScope: "student", sourceSnapshot: { storyboardTemplateVersion: "storyboard-v1", storyboardModel: { provider: "deterministic" } },
  rendererSnapshot: {}, scenes: [
    { sceneId: "scene-1", order: 1, kind: "objectives", title: "学习目标", narration: "理解等式", sourceRefs: [{ sourceRevisionId: "source-4", page: 214, quote: "等式两边" }] },
  ], status: "draft", durationSeconds: null, artifactSha256: null, consistencyReview: null,
  reviews: [], renderJob: null, createdAt: 1, publishedAt: null,
};

function mediaWith(overrides: Partial<LessonMedia>): LessonMedia {
  return { ...draft, ...overrides };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe("MediaStudioPanel teacher workflow", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("creates a source-linked draft from the currently published lesson", async () => {
    vi.mocked(listLessonMedia).mockResolvedValueOnce([]).mockResolvedValue([draft]);
    vi.mocked(createLessonMediaDraft).mockResolvedValue(draft);
    const user = userEvent.setup();
    render(<MediaStudioPanel chapterId="chapter-2" publicationId="publication-3" lessons={lessons} />);

    await user.click(await screen.findByRole("button", { name: "从发布讲解创建分镜" }));

    await waitFor(() => expect(createLessonMediaDraft).toHaveBeenCalledWith({
      chapterId: "chapter-2", publicationId: "publication-3", lessonId: "lesson-1", sourceRevisionId: "source-4",
    }));
    expect(await screen.findByRole("textbox", { name: "片段 1 · 学习目标" })).toHaveValue("理解等式");
    expect(screen.getByRole("button", { name: "提交受限渲染" })).toBeEnabled();
    await user.click(screen.getByText("来源快照与版本记录"));
    expect(screen.getByText("来源修订：source-4 · 发布：publication-3 · 分镜模板：storyboard-v1")).toBeVisible();
  });

  it("disables saving a revision after the teacher restores the original narration", async () => {
    // Given a draft with its original narration and no pending edits
    vi.mocked(listLessonMedia).mockResolvedValue([draft]);
    const user = userEvent.setup();
    render(<MediaStudioPanel chapterId="chapter-2" publicationId="publication-3" lessons={lessons} />);
    const narration = await screen.findByRole("textbox", { name: "片段 1 · 学习目标" });
    const saveButton = screen.getByRole("button", { name: "保存为新版本" });
    expect(saveButton).toBeDisabled();

    // When the teacher edits the narration and then restores its exact original text
    await user.clear(narration);
    await user.type(narration, "理解等式并复核");
    expect(saveButton).toBeEnabled();
    await user.clear(narration);
    await user.type(narration, "理解等式");

    // Then there is no new revision to save
    expect(narration).toHaveValue("理解等式");
    expect(saveButton).toBeDisabled();
  });

  it("does not restore a draft created for the previous lesson after the teacher switches lessons", async () => {
    // Given a draft request that remains pending while the teacher opens another lesson
    const pendingDraft = deferred<LessonMedia>();
    const secondLesson = mediaWith({ lessonId: "lesson-2", sourceRevisionId: "source-8", title: "第二课视频" });
    vi.mocked(listLessonMedia).mockImplementation((_chapterId, _publicationId, lessonId) =>
      Promise.resolve(lessonId === "lesson-2" ? [secondLesson] : []));
    vi.mocked(createLessonMediaDraft).mockReturnValue(pendingDraft.promise);
    const user = userEvent.setup();
    render(<MediaStudioPanel chapterId="chapter-2" publicationId="publication-3" lessons={[
      ...lessons,
      { lessonId: "lesson-2", title: "第二课", sourceRevisionId: "source-8" },
    ]} />);

    // When the first lesson's draft request finishes after the switch
    await user.click(await screen.findByRole("button", { name: "从发布讲解创建分镜" }));
    await waitFor(() => expect(createLessonMediaDraft).toHaveBeenCalled());
    await user.selectOptions(screen.getByRole("combobox", { name: "已发布教程" }), "lesson-2");
    // Then only the second lesson stays visible
    expect(await screen.findByRole("heading", { name: "第二课视频" })).toBeVisible();

    pendingDraft.resolve(mediaWith({ mediaId: "late-old-draft", title: "第一课迟到草稿" }));

    expect(await screen.findByRole("heading", { name: "第二课视频" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "第一课迟到草稿" })).not.toBeInTheDocument();
  });

  it("ignores a late video-list response from a previous publication", async () => {
    // Given separate unresolved list responses for two publication versions
    const oldPublication = deferred<LessonMedia[]>();
    const newPublication = deferred<LessonMedia[]>();
    vi.mocked(listLessonMedia).mockImplementation((_chapterId, publicationId) =>
      publicationId === "publication-old" ? oldPublication.promise : newPublication.promise);
    const { rerender } = render(<MediaStudioPanel chapterId="chapter-2" publicationId="publication-old" lessons={lessons} />);
    await waitFor(() => expect(listLessonMedia).toHaveBeenCalledWith("chapter-2", "publication-old", "lesson-1", expect.any(AbortSignal)));

    // When the newer publication loads first and the previous response arrives last
    rerender(<MediaStudioPanel chapterId="chapter-2" publicationId="publication-new" lessons={lessons} />);
    await waitFor(() => expect(listLessonMedia).toHaveBeenCalledWith("chapter-2", "publication-new", "lesson-1", expect.any(AbortSignal)));
    newPublication.resolve([mediaWith({ mediaId: "new-publication-video", publicationId: "publication-new", title: "新发布视频" })]);
    // Then the older publication cannot replace the selected result
    expect(await screen.findByRole("heading", { name: "新发布视频" })).toBeVisible();

    oldPublication.resolve([mediaWith({ mediaId: "old-publication-video", publicationId: "publication-old", title: "旧发布视频" })]);
    expect(await screen.findByRole("heading", { name: "新发布视频" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "旧发布视频" })).not.toBeInTheDocument();
  });

  it("keeps the selected version when an earlier version review finishes late", async () => {
    // Given two versions and a review action still pending for the first one
    const reviewResponse = deferred<LessonMedia>();
    const needsReview = mediaWith({ mediaId: "media-needs-review", status: "consistency_review_required", title: "待审版本" });
    const approved = mediaWith({ mediaId: "media-approved", version: 2, status: "approved", title: "已批准版本" });
    vi.mocked(listLessonMedia).mockResolvedValue([needsReview, approved]);
    vi.mocked(reviewLessonMedia).mockReturnValue(reviewResponse.promise);
    vi.mocked(publishLessonMedia).mockResolvedValue(mediaWith({ ...approved, status: "published" }));
    const user = userEvent.setup();
    render(<MediaStudioPanel chapterId="chapter-2" publicationId="publication-3" lessons={lessons} />);

    await screen.findByRole("heading", { name: "待审版本" });
    await user.click(screen.getByRole("button", { name: "批准此版本" }));
    await user.click(screen.getByRole("button", { name: /v2approved/ }));
    expect(await screen.findByRole("heading", { name: "已批准版本" })).toBeVisible();

    // When the teacher selects the second version before the first review returns
    reviewResponse.resolve(mediaWith({ ...needsReview, status: "approved" }));
    expect(await screen.findByRole("button", { name: "发布给学生" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "发布给学生" }));

    // Then publishing applies to the visible selected version
    await waitFor(() => expect(publishLessonMedia).toHaveBeenCalledWith("media-approved"));
    expect(screen.getByRole("heading", { name: "已批准版本" })).toBeVisible();
  });
});
