import type { Question } from "./types/index";

export function displayedPrompt(question: Question) {
  return question.prompt.replace(/[（(]\s*[)）]/g, "（ ）").trim();
}

export function optionText(option: string) {
  return option.replace(/^(?:\([A-D]\)|[A-D][.．:：、])\s*/, "").trim();
}
