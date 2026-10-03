// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LessonPlayer } from "./LessonPlayer";
import type { LessonDocument } from "../types/index";

const chapterLesson: LessonDocument = {
  lessonId: "lesson-1",
  title: "分数比较",
  version: 1,
  status: "published",
  knowledgePoints: ["分数比较"],
  blocks: [
    { id: "concept", type: "markdown", title: "核心概念", payload: { markdown: "同分母分数比较分子。" } },
    { id: "hint", type: "hint", title: "检查思路", payload: { level: 1, hint: "先确认分母相同。" } },
  ],
};

describe("LessonPlayer chapter document", () => {
  it("renders published chapter blocks through the shared player and lets the learner revisit a step", async () => {
    render(<LessonPlayer document={chapterLesson} studentMode />);

    expect(screen.getByRole("heading", { name: "分数比较" })).toBeVisible();
    expect(screen.getAllByText("同分母分数比较分子。")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "切换到步骤 2" }));
    expect(screen.getAllByText("先确认分母相同。")).toHaveLength(2);
  });
});
