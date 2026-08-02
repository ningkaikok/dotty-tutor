import type { LessonDocument, QuestionPayload } from "../types";

export function lessonDocumentFromPayload(payload: QuestionPayload): LessonDocument {
  return {
    lessonId: payload.question.id,
    title: payload.question.knowledgePoint,
    version: 1,
    status: payload.question.publicationStatus === "needs_review" ? "review" : "published",
    knowledgePoints: [payload.question.knowledgePoint],
    blocks: [
      ...payload.lessonSteps.map((step) => ({
        id: step.id,
        type: "diagram" as const,
        title: step.title,
        payload: {
          renderer: "geometry" as const,
          action: step.action,
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
  };
}
