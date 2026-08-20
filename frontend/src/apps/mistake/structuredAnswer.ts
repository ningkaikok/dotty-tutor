import type { Question, StructuredAnswerInput } from "../../types/index";

/** 将不同可视化作答控件统一转换为后端共享的结构化答案契约。 */
export function buildStructuredAnswer(
  question: Question,
  selectedOptions: string[],
  blankAnswers: Record<string, string>,
  numericAnswer: string,
): StructuredAnswerInput {
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
