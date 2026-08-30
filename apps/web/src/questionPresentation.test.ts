import { describe, expect, it } from "vitest";
import { displayedPrompt, optionText } from "./questionPresentation";
import type { Question } from "./types/index";

describe("question presentation", () => {
  it("normalizes an empty pair of parentheses and trims the prompt", () => {
    const question = { prompt: "  解方程（  ）  " } as Question;

    expect(displayedPrompt(question)).toBe("解方程（ ）");
  });

  it("removes supported option labels without changing the answer text", () => {
    expect(optionText("(A)  x > 0")).toBe("x > 0");
    expect(optionText("B．x < 0")).toBe("x < 0");
    expect(optionText("C: x = 1")).toBe("x = 1");
    expect(optionText("不带选项标签的答案")).toBe("不带选项标签的答案");
  });
});
