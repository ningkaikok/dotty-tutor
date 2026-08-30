import { describe, expect, it } from "vitest";
import { assembleSubQuestionText, hasMeaningfulSubQuestionAnswer } from "./answerAssembly";
import type { Question } from "./types/index";

const question = {
  id: "q1",
  chapter: "",
  knowledgePoint: "",
  prompt: "公共题干",
  givens: [],
  contentBlocks: [],
  subQuestions: [
    { id: "a", label: "（1）", prompt: "选择", questionType: "choice", evaluation: { mode: "deterministic" } },
    { id: "b", label: "（2）", prompt: "画线", questionType: "draw-line", evaluation: { mode: "tutor" } },
  ],
} as Question;

describe("sub-question answer assembly", () => {
  it("omits empty parts and preserves structured values", () => {
    const answers = { a: { selectedOptions: ["A"] }, b: { connections: [["p", "q"]] as Array<[string, string]> } };
    expect(hasMeaningfulSubQuestionAnswer(answers)).toBe(true);
    expect(assembleSubQuestionText(question, answers)).toBe("（1） A；（2） 我完成了画线作答");
    expect(assembleSubQuestionText(question, {})).toBe("");
  });
});
