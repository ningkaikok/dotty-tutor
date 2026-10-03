import type { Question, QuestionContentBlock } from "./types/index";

export function displayedPrompt(question: Question) {
  return question.prompt.replace(/[（(]\s*[)）]/g, "（ ）").trim();
}

export function optionText(option: string) {
  return option.replace(/^(?:\([A-D]\)|[A-D][.．:：、])\s*/, "").trim();
}

/** 旧题目可能在题干中残留整行 A-D；只有完整图片选项能佐证时才清理展示副本。 */
export function presentedContentBlocks(blocks: QuestionContentBlock[]): QuestionContentBlock[] {
  const options = blocks.find((block) => block.type === "options");
  if (!options || options.type !== "options" || options.items.length !== 4 || !options.items.every((item) => item.imageUrl)) return blocks;
  const labels = options.items.map((item) => item.label.replace(/[()（）]/g, "").trim());
  if (labels.join("") !== "ABCD") return blocks;
  const pattern = /^[ \t]*[（(]?([A-D])[）)]?[ \t]*$/gm;
  const stemBlocks = [...blocks].sort((a, b) => a.sourceOrder - b.sourceOrder).filter((block) => block.sourceOrder < options.sourceOrder);
  const bareLabels = stemBlocks.flatMap((block) => block.type === "text"
    ? [...block.text.matchAll(pattern)].map((match) => match[1]) : []);
  if (bareLabels.join("") !== labels.join("")) return blocks;
  return blocks.map((block) => block.type === "text" && block.sourceOrder < options.sourceOrder
    ? { ...block, text: block.text.replace(pattern, "").replace(/\n{3,}/g, "\n\n").trim() }
    : block);
}
