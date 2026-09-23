// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReviewTaskCard } from "./ReviewTaskCard";

describe("ReviewTaskCard", () => {
  afterEach(() => cleanup());

  it("does not present a superseded task as due today", () => {
    render(
      <ReviewTaskCard
        task={{
          taskId: "task-1",
          mistakeId: "mistake-1",
          learnerId: "learner-1",
          intervalDays: 14,
          dueAt: 1,
          status: "superseded",
          response: {},
          feedback: "",
          createdAt: 1,
          nextAction: "stay",
        }}
        serverTime={2}
        busy={false}
        onStart={vi.fn()}
        onAnswer={vi.fn()}
      />,
    );

    expect(screen.getByText("已被新计划替代")).toBeVisible();
    expect(screen.queryByText("今日待复习")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
