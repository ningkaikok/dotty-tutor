// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { MistakeItem } from "../../../types/index";
import { MistakeLibrary } from "./MistakeLibrary";

function item(): MistakeItem {
  return {
    mistakeId: "mistake-1",
    learnerId: "learner-1",
    sourceFilename: "练习",
    contentType: "text/plain",
    sourceImageUrl: "",
    questionPayload: {
      question: {
        id: "question-1",
        chapter: "第一章",
        knowledgePoint: "有理数",
        prompt: "计算 1 + 1",
        givens: [],
        contentBlocks: [],
      },
    },
    guideCards: [],
    originalAnswer: "3",
    subject: "数学",
    gradeBand: "初一",
    chapter: "第一章",
    knowledgePoint: "有理数",
    notes: "",
    status: "unmastered",
    createdAt: 1,
    updatedAt: 1,
  } as unknown as MistakeItem;
}

describe("MistakeLibrary archive confirmation", () => {
  afterEach(() => cleanup());

  it("user Given an unmastered mistake When the student cancels archiving Then the dialog explains what is cleared and nothing is archived", async () => {
    const user = userEvent.setup();
    const onArchive = vi.fn();
    render(
      <MistakeLibrary
        items={[item()]}
        loading={false}
        error=""
        onCapture={vi.fn()}
        onOpen={vi.fn()}
        onTutor={vi.fn()}
        onArchive={onArchive}
      />,
    );

    const archiveButton = screen.getByRole("button", { name: "归档" });
    await user.click(archiveButton);
    expect(screen.getByRole("dialog", { name: "确认归档这道错题？" })).toHaveAccessibleDescription(/归档后会清除这道题的辅导记录/);

    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onArchive).not.toHaveBeenCalled();
  });

  it("user Given an unmastered mistake When the student confirms archiving Then that mistake is archived", async () => {
    const user = userEvent.setup();
    const onArchive = vi.fn();
    render(
      <MistakeLibrary
        items={[item()]}
        loading={false}
        error=""
        onCapture={vi.fn()}
        onOpen={vi.fn()}
        onTutor={vi.fn()}
        onArchive={onArchive}
      />,
    );

    await user.click(screen.getByRole("button", { name: "归档" }));
    await user.click(screen.getByRole("button", { name: "确认归档" }));
    expect(onArchive).toHaveBeenCalledWith(expect.objectContaining({ mistakeId: "mistake-1" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
