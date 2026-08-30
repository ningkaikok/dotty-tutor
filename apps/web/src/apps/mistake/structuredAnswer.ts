import type { Question, StructuredAnswerInput, SubQuestionAnswer } from "../../types/index";

/** 将不同可视化作答控件统一转换为后端共享的结构化答案契约。 */
export function buildStructuredAnswer(
  question: Question,
  selectedOptions: string[],
  blankAnswers: Record<string, string>,
  numericAnswer: string,
  drawConnections: Array<[string, string]> = [],
  subQuestionAnswers: Record<string, SubQuestionAnswer> = {},
): StructuredAnswerInput {
  if (question.subQuestions?.length) {
    const content = question.subQuestions.map((part) => {
      const answer = subQuestionAnswers[part.id] ?? {};
      const value = answer.text
        ?? answer.numericAnswer
        ?? answer.selectedOptions?.join("、")
        ?? Object.values(answer.blankAnswers ?? {}).join("；")
        ?? (answer.connections?.length ? "已完成画线作答" : "");
      return `${part.label} ${value}`;
    }).join("；");
    return { content, interactionResult: { subQuestionAnswers } };
  }
  if (question.questionType === "draw-line") {
    return {
      content: drawConnections.length ? "我完成了画线作答" : "",
      interactionResult: { connections: drawConnections },
    };
  }
  if (question.questionType === "fill-blank") {
    return {
      content: Object.values(blankAnswers).join("；"),
      interactionResult: { blankAnswers },
    };
  }
  if (question.questionType === "numeric") {
    return { content: numericAnswer, interactionResult: { numericAnswer } };
  }
  return {
    content: selectedOptions.length ? `我选择${selectedOptions.join("、")}` : "",
    interactionResult: { selectedOptions },
  };
}
