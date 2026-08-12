import type { Question } from "./types";

export function hasImageOptions(question: Question) {
  // optionImageUrls 是新契约；四个纯标签配四张图是旧数据的兼容推断。
  const inferred = (question.imageUrls?.length === 4 || question.imageUrls?.length === 5)
    && question.options?.length === 4
    && question.options.every((option, index) => (
      option.trim() === `(${String.fromCharCode(65 + index)})`
      || option.trim() === `${String.fromCharCode(65 + index)}.`
      || option.trim() === `${String.fromCharCode(65 + index)}、`
    ));
  return Boolean(question.optionImageUrls?.length || inferred);
}

export function displayedPrompt(question: Question) {
  // 页面展示与数据库原始 prompt 分离。这里只移除已存在结构化 options 的重复尾部，
  // 不修改持久化数据，便于开发者继续查看 OCR/模型原始证据。
  let prompt = question.prompt;
  if (question.options?.length) {
    const matches = [...prompt.matchAll(/(?<![A-Za-z0-9])(?:\(([A-D])\)|([A-D])[.．:：、])\s*/g)];
    const labels = matches.map((match) => match[1] || match[2]);
    if (labels.slice(0, 4).join("") === "ABCD" && matches[0]?.index !== undefined) {
      prompt = prompt.slice(0, matches[0].index).trim();
    }
  }
  if (hasImageOptions(question)) {
    prompt = prompt.replace(/(^|\n)\s*\([A-D]\)\s*(?=\n|$)/g, "\n");
  }
  return prompt
    .replace(/!\[[^\]]*\]\(([^)]+)\)/g, "")
    .replace(/(?<![A-Za-z0-9_.-])(?:images\/|\/api\/uploads\/)[^\s)<>]+/g, "")
    .replace(/[（(]\s*[)）]/g, "（ ）")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function optionLabel(index: number) {
  return `(${String.fromCharCode(65 + index)})`;
}

export function optionText(option: string) {
  return option
    .replace(/^(?:\([A-D]\)|[A-D][.．:：、])\s*/, "")
    .replace(/!\[[^\]]*\]\(([^)]+)\)/, "")
    .replace(/(?<![A-Za-z0-9_.-])(?:images\/|\/api\/uploads\/)[^\s)<>]+/g, "")
    .trim();
}
