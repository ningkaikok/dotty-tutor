import type { ReactNode } from "react";
import type { MathContentBlock, QuestionContentBlock, TextContentBlock } from "./types";
import MathText from "./MathText";
import { stripLegacyImageText } from "./questionPresentation";

type InlineBlock = TextContentBlock | MathContentBlock;

interface QuestionContentProps {
  blocks: QuestionContentBlock[];
  selectedOption?: string | null;
  selectedOptions?: string[];
  multiple?: boolean;
  onSelectOption?: (label: string, answerText: string) => void;
  readOnly?: boolean;
}

function InlineContent({ blocks }: { blocks: InlineBlock[] }) {
  return (
    <>
      {blocks.map((block) => block.type === "text" ? (
        // 文本块也可能混有 `$...$`。统一交给 MathText，避免 OCR 结构化后
        // 同一条公式因为落在 text 或 math block 而出现两种显示结果。
        <MathText key={block.id} text={stripLegacyImageText(block.text)} />
      ) : (
        <MathText key={block.id} text={block.display ? `$$${block.latex}$$` : `$${block.latex}$`} />
      ))}
    </>
  );
}

const CHOICE_MARKER = /(?<![A-Za-z0-9])(?:\(([A-D])\)|([A-D])[.．:：、])\s*/g;
const MATH_FRAGMENT = /(\$\$[\s\S]+?\$\$|\$[^$]+?\$)/g;
// 旧版本会把图片文件名写入 options content block。这里只用于识别兼容数据，
// 实际展示清理由 questionPresentation 统一处理，避免不同页面出现不同清洗规则。
const LEGACY_IMAGE_MARKDOWN = /!\[[^\]]*\]\(([^)]+\.(?:jpg|jpeg|png|webp))\)/i;
const LEGACY_IMAGE_PATH = /(?:^|[(/])((?:images|api\/uploads)\/[^\s)]+\.(?:jpg|jpeg|png|webp))/i;

function legacyImageReference(text: string): string | null {
  const markdown = LEGACY_IMAGE_MARKDOWN.exec(text)?.[1];
  if (markdown) return markdown;
  return LEGACY_IMAGE_PATH.exec(text)?.[1] ?? null;
}

function extractLegacyChoiceValues(blocks: InlineBlock[]): string[] | null {
  // 只识别完整连续的 A/B/C/D，避免把正文中的变量 A、B 误判成选项。
  const serialized = blocks
    .map((block) => block.type === "text" ? block.text : `$${block.latex}$`)
    .join("");
  const matches = [...serialized.matchAll(CHOICE_MARKER)].slice(0, 4);
  const labels = matches.map((match) => match[1] || match[2]);
  if (labels.join("") !== "ABCD") return null;
  const values = matches.map((match, index) => (
    serialized.slice(match.index + match[0].length, matches[index + 1]?.index ?? serialized.length).trim()
  ));
  return values.every(Boolean) ? values : null;
}

function legacyValueBlocks(value: string, optionIndex: number): InlineBlock[] {
  return value.split(MATH_FRAGMENT).filter(Boolean).map((fragment, fragmentIndex) => {
    const display = fragment.startsWith("$$");
    const isMath = display || (fragment.startsWith("$") && fragment.endsWith("$"));
    return isMath ? {
      id: `legacy-option-${optionIndex}-math-${fragmentIndex}`,
      type: "math" as const,
      latex: fragment.slice(display ? 2 : 1, display ? -2 : -1).trim(),
      display,
      sourceOrder: fragmentIndex,
    } : {
      id: `legacy-option-${optionIndex}-text-${fragmentIndex}`,
      type: "text" as const,
      text: fragment,
      sourceOrder: fragmentIndex,
    };
  });
}

function stripDuplicatedChoiceTail(blocks: InlineBlock[], hasOptions: boolean): InlineBlock[] {
  // 新契约把选项放在 options block；旧数据可能同时把 A-D 留在题干，渲染前窄范围去重。
  if (!hasOptions) return blocks;
  const serialized = blocks
    .map((block) => block.type === "text" ? block.text : `$${block.latex}$`)
    .join("");
  const labels = [...serialized.matchAll(CHOICE_MARKER)].map((match) => match[1] || match[2]);
  if (labels.slice(0, 4).join("") !== "ABCD") return blocks;

  const trimmed: InlineBlock[] = [];
  for (const block of blocks) {
    if (block.type === "math") {
      trimmed.push(block);
      continue;
    }
    const marker = [...block.text.matchAll(CHOICE_MARKER)][0];
    if (!marker || marker.index === undefined) {
      trimmed.push(block);
      continue;
    }
    const text = block.text
      .slice(0, marker.index)
      .replace(/[（(]\s*[)）]/g, "（ ）")
      .trimEnd();
    if (text) trimmed.push({ ...block, text });
    break;
  }
  return trimmed;
}

export function QuestionContent({ blocks, selectedOption, selectedOptions = [], multiple = false, onSelectOption, readOnly = false }: QuestionContentProps) {
  // sourceOrder 是 OCR 版面顺序的稳定投影；不要依赖数组当前顺序，否则图片和选项可能互换。
  const nodes: ReactNode[] = [];
  let inlineBlocks: InlineBlock[] = [];
  const sortedBlocks = [...blocks].sort((left, right) => left.sourceOrder - right.sourceOrder);
  const optionsIndex = sortedBlocks.findIndex((block) => block.type === "options");
  const hasStructuredOptions = optionsIndex >= 0;
  const sourceImageBlocks = sortedBlocks.filter((block): block is Extract<QuestionContentBlock, { type: "image" }> => block.type === "image");
  const optionBlock = optionsIndex >= 0 && sortedBlocks[optionsIndex]?.type === "options"
    ? sortedBlocks[optionsIndex]
    : null;
  // 兼容已持久化的旧题：旧版本把“题干图 + 四张选项图”全部写成 stem image，
  // 同时把文件名写进 options。只在五图、四选项且选项内容确实是图片路径时推断，
  // 避免对普通文字选择题做宽泛猜测。
  const legacyOptionImageUrls = sourceImageBlocks.length === 5 && optionBlock?.items.length === 4
    && optionBlock.items.every((item) => item.contentBlocks.some((content) => (
      content.type === "text" && Boolean(legacyImageReference(content.text))
    )))
    ? sourceImageBlocks.slice(1).map((block) => block.url)
    : [];
  const promptBlocks = (hasStructuredOptions ? sortedBlocks.slice(0, optionsIndex) : sortedBlocks)
    .filter((block): block is InlineBlock => block.type === "text" || block.type === "math");
  const legacyChoiceValues = extractLegacyChoiceValues(promptBlocks);

  const flushInline = () => {
    if (!inlineBlocks.length) return;
    const current = stripDuplicatedChoiceTail(inlineBlocks, hasStructuredOptions);
    inlineBlocks = [];
    if (!current.length) return;
    nodes.push(
      <div className="question-prompt canonical-prompt" key={`prompt-${nodes.length}`}>
        <InlineContent blocks={current} />
      </div>,
    );
  };

  sortedBlocks.forEach((block) => {
    if (block.type === "text" || block.type === "math") {
      inlineBlocks.push(block);
      return;
    }
    flushInline();
    if (block.type === "image") {
      if (legacyOptionImageUrls.includes(block.url)) return;
      nodes.push(
        <div className="question-images" key={block.id}>
          <a href={block.url} target="_blank" rel="noreferrer" title="打开原始题图">
            <img src={block.url} alt="题干图片" loading="lazy" />
          </a>
        </div>,
      );
      return;
    }
    const containsOnlyLabels = block.items.every((item) => {
      const value = item.contentBlocks
        .map((content) => content.type === "text" ? content.text : content.latex)
        .join("")
        .trim();
      return !value || /^[（(]?[A-D][)）]?$/.test(value);
    });
    // 旧课程可能丢失结构化选项值，但题干仍保留完整 A-D。只恢复这一种明确形态，
    // 不能用前端猜测补全普通缺失选项。
    const displayItems = containsOnlyLabels && legacyChoiceValues?.length === block.items.length
      ? block.items.map((item, index) => ({
        ...item,
        contentBlocks: legacyValueBlocks(legacyChoiceValues[index], index),
      }))
      : block.items.map((item, index) => legacyOptionImageUrls[index]
        ? {
          ...item,
          imageUrl: legacyOptionImageUrls[index],
          contentBlocks: item.contentBlocks.filter((content) => !(
            content.type === "text" && Boolean(legacyImageReference(content.text))
          )),
        }
        : item);
    const hasImageOptions = displayItems.some((item) => item.imageUrl);
    // 短文字选项使用试卷式紧凑布局；图片或长句继续使用单列，保证可读性和点击区域。
    const compactOptions = !hasImageOptions && displayItems.every((item) => (
      item.contentBlocks
        .map((content) => content.type === "text" ? content.text : content.latex)
        .join("")
        .trim().length <= 18
    ));
    nodes.push(
      <ol
        className={`question-options exam-options ${hasImageOptions ? "image-options" : ""} ${compactOptions ? "compact-options" : ""}`}
        key={block.id}
      >
        {displayItems.map((item) => (
          <li key={item.label}>
            <button
              type="button"
              className={`question-option ${(multiple ? selectedOptions.includes(item.label) : selectedOption === item.label) ? "selected" : ""}`}
              onClick={readOnly ? undefined : () => onSelectOption?.(
                item.label,
                item.contentBlocks.map((content) => content.type === "text" ? content.text : content.latex).join(" "),
              )}
              disabled={readOnly}
              aria-pressed={multiple ? selectedOptions.includes(item.label) : selectedOption === item.label}
            >
              <span className="option-label">{item.label}</span>
              {item.imageUrl ? (
                <img src={item.imageUrl} alt={`选项${item.label}`} loading="lazy" />
              ) : (
                <span><InlineContent blocks={item.contentBlocks} /></span>
              )}
            </button>
          </li>
        ))}
      </ol>,
    );
  });
  flushInline();
  return <div className="canonical-question-content">{nodes}</div>;
}
