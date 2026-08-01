import type { ReactNode } from "react";
import type { MathContentBlock, QuestionContentBlock, TextContentBlock } from "./types";
import MathText from "./MathText";

type InlineBlock = TextContentBlock | MathContentBlock;

interface QuestionContentProps {
  blocks: QuestionContentBlock[];
  selectedOption?: string | null;
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

export function QuestionContent({ blocks, selectedOption, onSelectOption }: QuestionContentProps) {
  const nodes: ReactNode[] = [];
  let inlineBlocks: InlineBlock[] = [];

  const flushInline = () => {
    if (!inlineBlocks.length) return;
    const current = inlineBlocks;
    inlineBlocks = [];
    nodes.push(
      <div className="question-prompt canonical-prompt" key={`prompt-${nodes.length}`}>
        <InlineContent blocks={current} />
      </div>,
    );
  };

  [...blocks].sort((left, right) => left.sourceOrder - right.sourceOrder).forEach((block) => {
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
    const hasImageOptions = block.items.some((item) => item.imageUrl);
    nodes.push(
      <ol className={`question-options ${hasImageOptions ? "image-options" : ""}`} key={block.id}>
        {block.items.map((item) => (
          <li key={item.label}>
            <button
              type="button"
              className={`question-option ${selectedOption === item.label ? "selected" : ""}`}
              onClick={() => onSelectOption?.(
                item.label,
                item.contentBlocks.map((content) => content.type === "text" ? content.text : content.latex).join(" "),
              )}
              aria-pressed={selectedOption === item.label}
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
