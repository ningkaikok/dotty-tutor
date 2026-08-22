import type { LessonDocument, QuestionPayload } from "../types/index";

// 双端契约：此常量与 backend/domain/tutoring/checks.py 中同名常量必须保持一致。
// 两端各自判断"是否几何题"来决定画布动作的合法性，关键词列表漂移会导致
// 同一道题在内容生产端与学生端拿到不同的画布能力。
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
          // 当前题目不是几何题时，不允许模型内容携带不匹配的几何动作。
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
