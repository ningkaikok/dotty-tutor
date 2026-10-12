// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { PublishedChapter } from "../../types/chapter";
import { useChapterPublishedMedia } from "./useChapterPublishedMedia";

const { loadPublishedChapter } = vi.hoisted(() => ({ loadPublishedChapter: vi.fn() }));
vi.mock("../../api/chapters", () => ({ loadPublishedChapter }));

afterEach(() => { cleanup(); vi.clearAllMocks(); });

function snapshot(publicationId: string, title: string): PublishedChapter {
  return { chapterId: "chapter-1", publicationId, title, version: publicationId === "pub-old" ? 1 : 2, status: "published", teachingMode: "tutorial", subject: "math", lessons: [] };
}

function SnapshotStatus({ publicationId }: { publicationId: string }) {
  const media = useChapterPublishedMedia("chapter-1", publicationId);
  return <div>{media.loading ? "正在读取发布快照" : media.snapshot?.title ?? media.error}</div>;
}

it("user Given a teacher switches from an older publication while its snapshot is delayed When the newer snapshot arrives first Then the late old response cannot replace the selected version", async () => {
  let resolveOld!: (value: PublishedChapter) => void;
  let resolveNew!: (value: PublishedChapter) => void;
  loadPublishedChapter.mockImplementation((_chapterId: string, publicationId: string) => new Promise((resolve) => {
    if (publicationId === "pub-old") resolveOld = resolve;
    else resolveNew = resolve;
  }));

  const view = render(<SnapshotStatus publicationId="pub-old" />);
  expect(screen.getByText("正在读取发布快照")).toBeVisible();
  view.rerender(<SnapshotStatus publicationId="pub-new" />);
  expect(screen.getByText("正在读取发布快照")).toBeVisible();

  resolveNew(snapshot("pub-new", "当前已选发布版"));
  expect(await screen.findByText("当前已选发布版")).toBeVisible();
  resolveOld(snapshot("pub-old", "迟到的旧发布版"));
  await Promise.resolve();
  expect(screen.getByText("当前已选发布版")).toBeVisible();
  expect(screen.queryByText("迟到的旧发布版")).not.toBeInTheDocument();
});
