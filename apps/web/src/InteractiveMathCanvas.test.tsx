// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import type { TutorCanvasState } from "./types/tutoring";
import { InteractiveMathCanvas } from "./InteractiveMathCanvas";

const value: TutorCanvasState = {
  schemaVersion: "math-canvas-v1",
  kind: "point-placement",
  xMin: -5,
  xMax: 5,
  yMin: -5,
  yMax: 5,
  points: [{ id: "student", x: 1, y: 2 }],
  operations: [],
};

describe("InteractiveMathCanvas", () => {
  afterEach(cleanup);

  it("exposes the current point and supports arrow plus Space keyboard input", async () => {
    const user = userEvent.setup();
    let nextValue = value;
    const { rerender } = render(<InteractiveMathCanvas value={nextValue} onChange={(next) => { nextValue = next; }} />);
    const canvas = screen.getByRole("application", { name: "交互数学画布" });

    await user.click(canvas);
    await user.keyboard("{ArrowRight}");
    rerender(<InteractiveMathCanvas value={nextValue} onChange={(next) => { nextValue = next; }} />);
    await user.keyboard(" ");

    expect(canvas).toHaveAttribute("aria-valuetext", expect.stringContaining("当前点"));
    expect(nextValue.points[0].x).toBeGreaterThan(1);
    expect(nextValue.operations[nextValue.operations.length - 1]).toMatchObject({ source: "keyboard" });
  });
});
