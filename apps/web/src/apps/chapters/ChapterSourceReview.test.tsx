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
    { page: 3, text: "第一页原文。", regions: [{ regionId: "region-3-a", x: 0.1, y: 0.2, width: 0.3, height: 0.1 }] },
    { page: 4, text: "第二页依据。", regions: [{ regionId: "region-4-a", x: 0.2, y: 0.4, width: 0.4, height: 0.2 }] },
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

    fireEvent.click(screen.getByRole("button", { name: "回看第 3 页区域 1" }));

    expect(screen.getByRole("button", { name: "回看第 3 页区域 1" })).toHaveClass("active");
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledOnce());
  });
});
