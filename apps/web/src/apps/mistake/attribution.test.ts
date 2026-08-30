import { describe, expect, it } from "vitest";
import type { TutorMessage } from "../../types/index";
import { resolveMistakeAttribution } from "./attribution";

function message(category: string, needsConfirmation: boolean): TutorMessage {
  return {
    messageId: category,
    threadId: "thread-1",
    role: "assistant",
    content: "分析",
    inputMode: "text",
    action: { tutorTurnPlan: { misconception: { category, needsConfirmation } } },
    modelRun: {},
    createdAt: 1,
  };
}

function aiAssessmentOf(messages: TutorMessage[]) {
  return resolveMistakeAttribution(undefined, messages).aiAssessment;
}

describe("resolveMistakeAttribution", () => {
  it("只采信明确通过门禁的合法分类", () => {
    expect(aiAssessmentOf([
      message("concept", true),
      message("unknown", false),
      message("calculation", false),
    ])).toBe("calculation");
  });

  it("多条消息时以最后一条可信判断为准", () => {
    expect(aiAssessmentOf([
      message("concept", false),
      message("reading", true),
      message("careless", false),
    ])).toBe("careless");
  });

  it("过门禁的 unknown 不是判断，不予采信", () => {
    expect(aiAssessmentOf([message("unknown", false)])).toBeUndefined();
  });

  it("没有可信判断时返回 undefined", () => {
    expect(aiAssessmentOf([
      message("concept", true),
      message("unknown", false),
      message("not-a-category", false),
    ])).toBeUndefined();
  });

  it("没有消息时只保留学生自评", () => {
    expect(resolveMistakeAttribution("reading")).toEqual({
      selfAssessment: "reading",
      aiAssessment: undefined,
    });
  });

  it("保留学生自评并附上最新可信 AI 归因", () => {
    expect(resolveMistakeAttribution("calculation", [message("concept", false)])).toEqual({
      selfAssessment: "calculation",
      aiAssessment: "concept",
    });
  });
});
