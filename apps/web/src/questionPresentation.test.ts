import { describe, expect, it } from "vitest";
import { displayedPrompt, optionText, presentedContentBlocks } from "./questionPresentation";
import type { Question, QuestionContentBlock } from "./types/index";

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

const imageChoices: QuestionContentBlock = {
  id: "options", type: "options", sourceOrder: 1,
  items: ["A", "B", "C", "D"].map((label) => ({ label: `(${label})`, contentBlocks: [], imageUrl: `/assets/${label}.png` })),
};
describe("historical image choice presentation", () => {
  it("removes standalone OCR labels only when all corresponding image choices exist", () => {
    const blocks: QuestionContentBlock[] = [{ id: "stem", type: "text", sourceOrder: 0, text: "选择图形\nABCD是四边形\n\nA\n\nB\n\nC\n\nD" }, imageChoices];
    expect(presentedContentBlocks(blocks)).toEqual([{ ...blocks[0], text: "选择图形\nABCD是四边形" }, imageChoices]);
    expect((blocks[0] as { text: string }).text).toContain("\n\nA");
  });
  it("preserves incomplete label sequences and text choices", () => {
    const stem: QuestionContentBlock = { id: "stem", type: "text", sourceOrder: 0, text: "点的名称\nA\nB" };
    expect(presentedContentBlocks([stem, imageChoices])).toEqual([stem, imageChoices]);
    const textChoices = { ...imageChoices, items: imageChoices.items.map((item) => ({ ...item, imageUrl: undefined })) };
    const labels: QuestionContentBlock = { ...stem, text: "点的名称\nA\nB\nC\nD" };
    expect(presentedContentBlocks([labels, textChoices])).toEqual([labels, textChoices]);
  });
});
