// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import MediaPlayer from "./MediaPlayer";
import { listPublishedLessonMedia } from "../../api/lessonMedia";

vi.mock("../../api/lessonMedia", () => ({
  listPublishedLessonMedia: vi.fn(),
  lessonMediaPublicAssetUrl: (path: string) => path,
}));

const props = { chapterId: "chapter-1", publicationId: "publication-2", lessonId: "lesson-3", sourceRevisionId: "source-4" };

describe("MediaPlayer learner video", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("renders nothing when the current published lesson has no approved video", async () => {
    vi.mocked(listPublishedLessonMedia).mockResolvedValue({ ...props, items: [] });
    const { container } = render(<MediaPlayer {...props} />);
    await waitFor(() => expect(listPublishedLessonMedia).toHaveBeenCalledWith(
      props.chapterId, props.publicationId, props.lessonId, expect.any(AbortSignal),
    ));
    expect(container).toBeEmptyDOMElement();
  });

  it("shows captions, source attribution, and a downloadable VTT for a published video", async () => {
    vi.mocked(listPublishedLessonMedia).mockResolvedValue({
      ...props,
      items: [{
        mediaId: "media-5", version: 2, title: "解一元一次方程", subject: "math",
        sourceRevisionId: props.sourceRevisionId, sourceTitle: "Elementary Algebra 2e",
        sourceUrl: "https://openstax.org/details/books/elementary-algebra-2e",
        attributionText: "Access for free at openstax.org.", durationSeconds: 76,
        videoUrl: "/api/chapters/chapter-1/media/published/media-5/asset",
        captionsUrl: "/api/chapters/chapter-1/media/published/media-5/captions",
        captionsDownloadUrl: "/api/chapters/chapter-1/media/published/media-5/captions/download",
      }],
    });
    const { container } = render(<MediaPlayer {...props} />);

    expect(await screen.findByRole("heading", { name: "解一元一次方程" })).toBeVisible();
    const video = screen.getByLabelText("解一元一次方程教学视频");
    expect(video.querySelector("source")?.getAttribute("src")).toContain("/media-5/asset");
    const track = video.querySelector("track");
    expect(track).toHaveAttribute("kind", "captions");
    expect(track).toHaveAttribute("default");
    expect(track).toHaveAttribute("srcLang", "zh");
    expect(screen.getByRole("link", { name: "下载字幕（WebVTT）" })).toHaveAttribute("href", "/api/chapters/chapter-1/media/published/media-5/captions/download");
    expect(screen.getByRole("link", { name: "下载字幕（WebVTT）" })).toHaveAttribute("download", "media-5.vtt");
    expect(screen.getByRole("link", { name: "查看教材来源" })).toHaveAttribute("href", "https://openstax.org/details/books/elementary-algebra-2e");
    expect(container).toHaveTextContent("Access for free at openstax.org.");
  });

  it("does not show a video whose source revision differs from the visible lesson", async () => {
    vi.mocked(listPublishedLessonMedia).mockResolvedValue({
      ...props,
      items: [{
        mediaId: "stale", version: 1, title: "旧来源媒体", subject: "math", sourceRevisionId: "old-source",
        sourceTitle: null, sourceUrl: null, attributionText: null, durationSeconds: 64,
        videoUrl: "/asset", captionsUrl: "/captions",
        captionsDownloadUrl: "/captions/download",
      }],
    });
    const { container } = render(<MediaPlayer {...props} />);
    await waitFor(() => expect(listPublishedLessonMedia).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
