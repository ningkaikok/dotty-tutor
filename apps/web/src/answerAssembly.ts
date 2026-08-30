import type { Question, SubQuestionAnswer } from "./types/index";

function answerValue(answer: SubQuestionAnswer): string {
  if (answer.text?.trim()) return answer.text.trim();
  if (answer.numericAnswer?.trim()) return answer.numericAnswer.trim();
  if (answer.selectedOptions?.length) return answer.selectedOptions.join("、");
  const blanks = Object.values(answer.blankAnswers ?? {}).filter((value) => value.trim());
  if (blanks.length) return blanks.join("；");
  if (answer.connections?.length) return "我完成了画线作答";
  return "";
}

export function hasMeaningfulSubQuestionAnswer(answers: Record<string, SubQuestionAnswer>): boolean {
  return Object.values(answers).some((answer) => Boolean(answerValue(answer)));
}

/** Build natural-language context without inventing labels for empty parts. */
export function assembleSubQuestionText(
  question: Question,
  answers: Record<string, SubQuestionAnswer>,
): string {
  return (question.subQuestions ?? [])
    .map((part) => {
      const value = answerValue(answers[part.id] ?? {});
      return value ? `${part.label} ${value}` : "";
    })
    .filter(Boolean)
    .join("；");
}
