import type { ReactNode } from "react";
import type { MathContentBlock, QuestionContentBlock, TextContentBlock } from "./types";
import MathText from "./MathText";

type InlineBlock = TextContentBlock | MathContentBlock;

interface QuestionContentProps {
  blocks: QuestionContentBlock[];
  selectedOption?: string | null;
  selectedOptions?: string[];
  multiple?: boolean;
  onSelectOption?: (label: string, answerText: string) => void;
}

function InlineContent({ blocks }: { blocks: InlineBlock[] }) {
  return (
    <>
      {blocks.map((block) => block.type === "text" ? (
        <span key={block.id}>{block.text}</span>
      ) : (
        <MathText key={block.id} text={block.display ? `$$${block.latex}$$` : `$${block.latex}$`} />
      ))}
    </>
  );
}

const CHOICE_MARKER = /(?<![A-Za-z0-9])(?:\(([A-D])\)|([A-D])[.．:：、])\s*/g;
const MATH_FRAGMENT = /(\$\$[\s\S]+?\$\$|\$[^$]+?\$)/g;

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

export function QuestionContent({ blocks, selectedOption, selectedOptions = [], multiple = false, onSelectOption }: QuestionContentProps) {
  // sourceOrder 是 OCR 版面顺序的稳定投影；不要依赖数组当前顺序，否则图片和选项可能互换。
  const nodes: ReactNode[] = [];
  let inlineBlocks: InlineBlock[] = [];
  const sortedBlocks = [...blocks].sort((left, right) => left.sourceOrder - right.sourceOrder);
  const optionsIndex = sortedBlocks.findIndex((block) => block.type === "options");
  const hasStructuredOptions = optionsIndex >= 0;
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
      : block.items;
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
              onClick={() => onSelectOption?.(
                item.label,
                item.contentBlocks.map((content) => content.type === "text" ? content.text : content.latex).join(" "),
              )}
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
