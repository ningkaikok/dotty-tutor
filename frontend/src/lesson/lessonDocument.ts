import type { LessonDocument, QuestionPayload } from "../types";

const GEOMETRY_MARKERS = ["几何", "三角形", "垂直", "平分线", "轨迹", "圆", "角平分", "中点", "全等"];

function isGeometryQuestion(payload: QuestionPayload) {
  if (payload.question.questionType === "draw-line") return true;
  const question = payload.question;
  const text = [question.chapter, question.knowledgePoint, question.prompt, ...question.givens]
    .join(" ")
    .toLowerCase();
  return GEOMETRY_MARKERS.some((marker) => text.includes(marker));
}

export function lessonDocumentFromPayload(payload: QuestionPayload, sourceUploadId?: string): LessonDocument {
  const geometryQuestion = isGeometryQuestion(payload);
  return {
    lessonId: payload.question.id,
    title: payload.question.knowledgePoint,
    version: 1,
    status: payload.question.publicationStatus === "needs_review" ? "in_review" : "draft",
    sourceUploadId,
    knowledgePoints: [payload.question.knowledgePoint],
    blocks: [
      ...payload.lessonSteps.map((step) => ({
        id: step.id,
        type: "diagram" as const,
        title: step.title,
        payload: {
          renderer: "geometry" as const,
          // 兼容已经持久化的旧题目：非几何题不允许沿用历史三角形动作。
          action: geometryQuestion ? step.action : "show-base",
          text: step.text,
          speechText: step.speechText,
        },
      })),
      {
        id: `${payload.question.id}-quiz`,
        type: "quiz" as const,
        title: "课后练习",
        payload: { questionId: payload.question.id },
      },
    ],
    questionPayload: payload as unknown as Record<string, unknown>,
    guideCards: [],
  };
}
