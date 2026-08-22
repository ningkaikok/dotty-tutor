import { describe, expect, it } from "vitest";
import { lessonDocumentFromPayload } from "./lessonDocument";
import type { QuestionPayload } from "../types/index";

function payload(overrides: {
  questionType?: string;
  prompt?: string;
  chapter?: string;
  knowledgePoint?: string;
  publicationStatus?: string;
  stepAction?: string;
}): QuestionPayload {
  return {
    question: {
      id: "q-geometry-1",
      questionType: overrides.questionType ?? "choice",
      prompt: overrides.prompt ?? "求证：两个三角形全等。",
      knowledgePoint: overrides.knowledgePoint ?? "全等三角形",
      chapter: overrides.chapter ?? "几何",
      publicationStatus: overrides.publicationStatus ?? "ready",
      givens: [],
      options: [],
      imageUrls: [],
    },
    lessonSteps: [
      { id: "step-1", title: "第一步", action: overrides.stepAction ?? "show-base", text: "观察图形。", speechText: "先观察。" },
    ],
  } as unknown as QuestionPayload;
}

describe("lessonDocumentFromPayload", () => {
  it("draw-line 题视为几何题，保留模型给出的画布动作", () => {
    const doc = lessonDocumentFromPayload(payload({ questionType: "draw-line", stepAction: "show-triangles" }));
    const diagram = doc.blocks.find((block) => block.type === "diagram");
    expect(doc.blocks[0].payload.action).toBe("show-triangles");
    expect(diagram).toBeDefined();
  });

  it("非几何题强制 show-base：不允许模型内容携带不匹配的几何动作", () => {
    const doc = lessonDocumentFromPayload(payload({
      prompt: "解一元一次方程。",
      chapter: "方程",
      knowledgePoint: "移项",
      stepAction: "show-triangles",
    }));
    expect(doc.blocks[0].payload.action).toBe("show-base");
  });

  it("needs_review 映射为 in_review，其余为 draft", () => {
    expect(lessonDocumentFromPayload(payload({})).status).toBe("draft");
    expect(lessonDocumentFromPayload(payload({ publicationStatus: "needs_review" })).status).toBe("in_review");
  });

  it("末尾追加指向题目 ID 的 quiz 块", () => {
    const doc = lessonDocumentFromPayload(payload({}));
    const last = doc.blocks.at(-1);
    expect(last?.type).toBe("quiz");
    expect(last?.id).toBe("q-geometry-1-quiz");
  });
});
