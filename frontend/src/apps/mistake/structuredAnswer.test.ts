import { describe, expect, it } from "vitest";
import { buildStructuredAnswer } from "./structuredAnswer";
import type { Question } from "../../types/index";

function question(questionType: Question["questionType"]): Question {
  return {
    id: "q-1",
    questionType,
    prompt: "题干",
    options: [],
    givens: [],
    knowledgePoint: "一元一次方程",
    chapter: "代数",
    imageUrls: [],
    contentBlocks: [],
    lessonSteps: [],
    publicationStatus: "ready",
    answerSpec: undefined,
    correctAnswer: undefined,
    correctAnswers: [],
    interaction: {},
  } as unknown as Question;
}

describe("buildStructuredAnswer", () => {
  it("fill-blank 把各空答案拼进 content 并透传 blankAnswers", () => {
    const result = buildStructuredAnswer(
      question("fill-blank"),
      [],
      { b1: "4", b2: "-1" },
      "",
    );
    expect(result.content).toBe("4；-1");
    expect(result.interactionResult).toEqual({ blankAnswers: { b1: "4", b2: "-1" } });
  });

  it("numeric 使用数值作答字段", () => {
    const result = buildStructuredAnswer(question("numeric"), [], {}, "41.8");
    expect(result).toEqual({ content: "41.8", interactionResult: { numericAnswer: "41.8" } });
  });

  it("选择/多选拼接可读文案并携带完整选项集合", () => {
    const result = buildStructuredAnswer(question("multi-select"), ["A", "C"], {}, "");
    expect(result.content).toBe("我选择A、C");
    expect(result.interactionResult).toEqual({ selectedOptions: ["A", "C"] });
  });

  it("未做任何选择时 content 为空字符串而不是占位文案", () => {
    const result = buildStructuredAnswer(question("choice"), [], {}, "");
    expect(result.content).toBe("");
    expect(result.interactionResult).toEqual({ selectedOptions: [] });
  });
});
