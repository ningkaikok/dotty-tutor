import type { Question } from "./types";

export function hasImageOptions(question: Question) {
  const inferred = question.imageUrls?.length === 4
    && question.options?.length === 4
    && question.options.every((option, index) => option.trim() === `(${String.fromCharCode(65 + index)})`);
  return Boolean(question.optionImageUrls?.length || inferred);
}

export function displayedPrompt(question: Question) {
  let prompt = question.prompt;
  if (question.options?.length) {
    prompt = prompt.replace(/\(A\)[\s\S]*$/, "").trim();
  }
  if (hasImageOptions(question)) {
    prompt = prompt.replace(/(^|\n)\s*\([A-D]\)\s*(?=\n|$)/g, "\n");
  }
  return prompt.replace(/\n{3,}/g, "\n\n").trim();
}

export function optionLabel(index: number) {
  return `(${String.fromCharCode(65 + index)})`;
}

export function optionText(option: string) {
  return option.replace(/^\([A-D]\)\s*/, "").replace(/!\[[^\]]*\]\(([^)]+)\)/, "").trim();
}
