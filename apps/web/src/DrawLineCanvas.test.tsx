// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import type { QuestionInteraction } from "./types/index";
import { DrawLineCanvas } from "./DrawLineCanvas";

const interaction: QuestionInteraction = {
  type: "draw-line",
  instruction: "连接对应端点",
  points: [
    { id: "left", label: "A", x: 0.2, y: 0.5 },
    { id: "right", label: "B", x: 0.8, y: 0.5 },
  ],
  requiredConnections: [["left", "right"]],
};

describe("DrawLineCanvas", () => {
  afterEach(cleanup);

  it("allows keyboard users to select both endpoints with Enter and Space", async () => {
    const user = userEvent.setup();
    let connections: Array<[string, string]> = [];
    const { rerender } = render(
      <DrawLineCanvas interaction={interaction} connections={connections} onChange={(next) => { connections = next; }} />,
    );
    const first = screen.getByRole("button", { name: "端点 A" });
    const second = screen.getByRole("button", { name: "端点 B" });

    first.focus();
    await user.keyboard("{Enter}");
    rerender(<DrawLineCanvas interaction={interaction} connections={connections} onChange={(next) => { connections = next; }} />);
    second.focus();
    await user.keyboard(" ");

    expect(connections).toEqual([["left", "right"]]);
    expect(first).toHaveAttribute("aria-pressed", "false");
  });

  it("keeps read-only endpoints out of the tab order", () => {
    render(<DrawLineCanvas interaction={interaction} connections={[]} onChange={() => undefined} readOnly />);
    expect(screen.getByRole("button", { name: "端点 A" })).toHaveAttribute("tabindex", "-1");
    expect(screen.getByRole("button", { name: "端点 B" })).toHaveAttribute("tabindex", "-1");
  });
});
