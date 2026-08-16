import type { MathContentBlock, Question, QuestionContentBlock, TextContentBlock } from "./types";

const LEGACY_IMAGE_MARKDOWN = /!\[[^\]]*\]\([^)]*\.(?:jpg|jpeg|png|webp)\)/gi;
const LEGACY_IMAGE_PATH = /(?:\(\s*)?(?:\/?)(?:images|api\/uploads)\/[^\s)<>]+\.(?:jpg|jpeg|png|webp)(?:\s*\))?/gi;
const INLINE_IMAGE_REFERENCE = /!\[[^\]]*\]\(([^)]+)\)/i;
const PROMPT_FRAGMENT = /(!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)|\$\$[\s\S]+?\$\$|\$[^$]+?\$)/g;

/**
 * 清理历史题目里被错误持久化为普通文本的图片引用。
 *
 * 新题目通过 contentBlocks/imageUrl 渲染图片；这里仅处理旧数据的只读展示，
 * 不修改数据库，避免把“迁移兼容”再次混入生成和审核流程。
 */
export function stripLegacyImageText(text: string) {
  return text
    .replace(LEGACY_IMAGE_MARKDOWN, "")
    .replace(LEGACY_IMAGE_PATH, "")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/\s+([，。；：！？、])/g, "$1");
}

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

function promptTextForRendering(question: Question) {
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
    .replace(/[（(]\s*[)）]/g, "（ ）")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function displayedPrompt(question: Question) {
  // 页面展示与数据库原始 prompt 分离。这里只移除历史图片文字和重复选项尾部。
  return stripLegacyImageText(promptTextForRendering(question));
}

export function optionLabel(index: number) {
  return `(${String.fromCharCode(65 + index)})`;
}

export function optionText(option: string) {
  return option
    .replace(/^(?:\([A-D]\)|[A-D][.．:：、])\s*/, "")
    .replace(LEGACY_IMAGE_MARKDOWN, "")
    .replace(LEGACY_IMAGE_PATH, "")
    .trim();
}

function legacyTextBlock(text: string, id: string, sourceOrder: number): TextContentBlock {
  return { id, type: "text", text, sourceOrder };
}

function legacyOptionBlocks(text: string, optionIndex: number): Array<TextContentBlock | MathContentBlock> {
  // The legacy fields store formulas inline. Split only explicit delimiters here; MathText
  // remains the single renderer for any Markdown image or formula left inside text blocks.
  const fragments = text.split(/(\$\$[\s\S]+?\$\$|\$[^$]+?\$)/g).filter(Boolean);
  return fragments.map((fragment, index) => {
    const display = fragment.startsWith("$$");
    const math = display || (fragment.startsWith("$") && fragment.endsWith("$"));
    return math
      ? {
          id: `legacy-option-${optionIndex}-math-${index}`,
          type: "math" as const,
          latex: fragment.slice(display ? 2 : 1, display ? -2 : -1).trim(),
          display,
          sourceOrder: index,
        }
      : legacyTextBlock(fragment, `legacy-option-${optionIndex}-text-${index}`, index);
  });
}

function legacyImageBlock(url: string, id: string, sourceOrder: number): QuestionContentBlock {
  return {
    id,
    type: "image",
    url,
    assetId: url,
    sourceReference: url,
    role: "stem",
    sourceOrder,
  };
}

function legacyPromptBlocks(question: Question, seenImages: Set<string>): QuestionContentBlock[] {
  const blocks: QuestionContentBlock[] = [];
  const prompt = promptTextForRendering(question);
  let cursor = 0;
  let sourceOrder = 0;
  for (const match of prompt.matchAll(PROMPT_FRAGMENT)) {
    const index = match.index ?? 0;
    if (index > cursor) {
      const text = stripLegacyImageText(prompt.slice(cursor, index));
      if (text) blocks.push(legacyTextBlock(text, `${question.id}-prompt-${sourceOrder}`, sourceOrder++));
    }
    const fragment = match[0];
    const image = /^!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)$/.exec(fragment);
    if (image) {
      if (!seenImages.has(image[2])) {
        seenImages.add(image[2]);
        blocks.push(legacyImageBlock(image[2], `${question.id}-prompt-image-${sourceOrder}`, sourceOrder));
      }
      sourceOrder++;
    } else {
      const display = fragment.startsWith("$$");
      blocks.push({
        id: `${question.id}-prompt-math-${sourceOrder}`,
        type: "math",
        latex: fragment.slice(display ? 2 : 1, display ? -2 : -1).trim(),
        display,
        sourceOrder: sourceOrder++,
      });
    }
    cursor = index + fragment.length;
  }
  if (cursor < prompt.length) {
    const text = stripLegacyImageText(prompt.slice(cursor));
    if (text) blocks.push(legacyTextBlock(text, `${question.id}-prompt-${sourceOrder}`, sourceOrder));
  }
  return blocks;
}

/**
 * Convert both the current content-block contract and old prompt/options/image fields into
 * one renderable sequence. This is the compatibility boundary: pages never need to guess
 * whether an image came from `contentBlocks`, `imageUrls`, or `optionImageUrls`.
 */
export function questionContentBlocks(question: Question): QuestionContentBlock[] {
  if (question.contentBlocks?.length) return question.contentBlocks;

  const blocks: QuestionContentBlock[] = [];
  const seenImages = new Set<string>();
  blocks.push(...legacyPromptBlocks(question, seenImages));

  const imageChoices = hasImageOptions(question);
  const stemImages = question.optionImageUrls?.length
    ? (question.imageUrls ?? [])
    : imageChoices && question.imageUrls?.length === 5
    ? question.imageUrls.slice(0, 1)
    : imageChoices ? [] : (question.imageUrls ?? []);
  stemImages.forEach((url, index) => {
    if (seenImages.has(url)) return;
    seenImages.add(url);
    blocks.push(legacyImageBlock(url, `${question.id}-image-${index}`, blocks.length + index));
  });

  if (question.options?.length) {
    const optionImages = question.optionImageUrls
      ?? (imageChoices ? (question.imageUrls?.length === 5 ? question.imageUrls.slice(1) : question.imageUrls) : [])
      ?? [];
    blocks.push({
      id: `${question.id}-options`,
      type: "options",
      sourceOrder: blocks.length + 1,
      items: question.options.map((option, index) => {
        const imageUrl = optionImages[index] || INLINE_IMAGE_REFERENCE.exec(option)?.[1];
        return {
          label: optionLabel(index),
          contentBlocks: legacyOptionBlocks(optionText(option), index),
          ...(imageUrl ? { imageUrl, assetId: imageUrl } : {}),
        };
      }),
    });
  }
  return blocks;
}
