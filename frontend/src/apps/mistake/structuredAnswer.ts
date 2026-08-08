import type { Question, StructuredAnswerInput } from "../../types";

/** Translate visible answer controls into the backend's shared contract. */
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
