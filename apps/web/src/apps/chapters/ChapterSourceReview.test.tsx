// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ChapterSourceRevision } from "../../types/chapter";
import { ChapterSourceReview } from "./ChapterSourceReview";

const revisions: ChapterSourceRevision[] = [{
  createdAt: 1,
  sourceRevisionId: "source-v1",
  fingerprint: "fingerprint-123456789",
  sourceVersion: "edition-2",
  license: "校内授权",
  pageStart: 3,
  pageEnd: 4,
  pages: [
    { page: 3, text: "第一页原文。", previewUrl: "/api/chapters/ch-1/sources/source-v1/pages/3/preview", regions: [{ regionId: "region-3-a", x: 0.1, y: 0.2, width: 0.3, height: 0.1 }] },
    { page: 4, text: "第二页依据。", previewUrl: null, regions: [{ regionId: "region-4-a", x: 0.2, y: 0.4, width: 0.4, height: 0.2 }] },
  ],
  issues: [],
}];

describe("ChapterSourceReview", () => {
  afterEach(cleanup);

  it("user Given a lesson points to a region on page four When the teacher follows its source link Then that page and region are brought into view", async () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
    render(<ChapterSourceReview revisions={revisions} locator={{ sourceRevisionId: "source-v1", page: 4, regions: revisions[0].pages[1].regions ?? [] }} />);

    fireEvent.click(screen.getByRole("button", { name: "回看课程依据：第 4 页" }));

    expect(screen.getByRole("article", { name: "来源第 4 页" })).toHaveClass("focused");
    expect(screen.getByText("第二页依据。")).toBeInTheDocument();
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledOnce());
  });

  it("user Given multiple source pages When choosing a page region Then the source view highlights the selected region", async () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
    render(<ChapterSourceReview revisions={revisions} />);

    fireEvent.click(screen.getByRole("button", { name: "回看第 3 页原图区域 1" }));

    expect(screen.getByRole("button", { name: "回看第 3 页原图区域 1" })).toHaveClass("active");
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledOnce());
  });

  it("user Given an authorized original-page preview When reviewing the source Then normalized regions overlay the real page and can be enlarged", () => {
    render(<ChapterSourceReview revisions={revisions} />);
    const image = screen.getByRole("img", { name: "教材原页，第 3 页" });
    expect(image).toHaveAttribute("src", "/api/chapters/ch-1/sources/source-v1/pages/3/preview");
    expect(screen.getByRole("group", { name: "第 3 页原图区域定位" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "放大查看原页" }));
    expect(screen.getByRole("dialog", { name: "教材原页，第 3 页" })).toBeInTheDocument();
  });

  it("user Given a manually pasted source without an original page When reviewing it Then the interface clearly says only text can be reviewed", () => {
    render(<ChapterSourceReview revisions={revisions} />);
    expect(screen.getByText("此来源没有可用的教材原页图片，当前仅供文字复核。")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "教材原页，第 4 页" })).not.toBeInTheDocument();
  });

  it("user Given the original-page preview fails to load When reviewing that source Then no diagram is shown as if it were the page", () => {
    render(<ChapterSourceReview revisions={revisions} />);
    fireEvent.error(screen.getByRole("img", { name: "教材原页，第 3 页" }));
    expect(screen.getByText("原页图片加载失败，无法显示教材预览。")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "教材原页，第 3 页" })).not.toBeInTheDocument();
  });
});
