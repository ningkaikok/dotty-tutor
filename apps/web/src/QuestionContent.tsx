import type { ReactNode } from "react";
import type { MathContentBlock, QuestionContentBlock, TextContentBlock } from "./types/index";
import MathText from "./MathText";
import { RichText } from "./RichText";

type InlineBlock = TextContentBlock | MathContentBlock;

interface QuestionContentProps {
  blocks: QuestionContentBlock[];
  selectedOption?: string | null;
  selectedOptions?: string[];
  multiple?: boolean;
  onSelectOption?: (label: string, answerText: string) => void;
  readOnly?: boolean;
  showOptions?: boolean;
}

function InlineContent({ blocks }: { blocks: InlineBlock[] }) {
  return <>{blocks.map((block) => block.type === "text"
    ? <RichText key={block.id} text={block.text} />
    : <MathText key={block.id} text={block.display ? `$$${block.latex}$$` : `$${block.latex}$`} />)}</>;
}

export function QuestionContent({
  blocks,
  selectedOption,
  selectedOptions = [],
  multiple = false,
  onSelectOption,
  readOnly = false,
  showOptions = true,
}: QuestionContentProps) {
  const nodes: ReactNode[] = [];
  let inlineBlocks: InlineBlock[] = [];
  const flushInline = () => {
    if (!inlineBlocks.length) return;
    nodes.push(
      <div className="question-prompt canonical-prompt" key={`prompt-${nodes.length}`}>
        <InlineContent blocks={inlineBlocks} />
      </div>,
    );
    inlineBlocks = [];
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
    if (block.type === "table") {
      nodes.push(
        <div className="question-table-wrapper" key={block.id}>
          <table className="question-table">
            <tbody>
              {block.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.cells.map((cell, cellIndex) => (
                    <td key={cellIndex}>
                      <InlineContent blocks={cell.contentBlocks} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      return;
    }
    if (!showOptions) return;
    const hasImageOptions = block.items.some((item) => item.imageUrl);
    const compactOptions = !hasImageOptions && block.items.every((item) => (
      item.contentBlocks.map((content) => content.type === "text" ? content.text : content.latex).join("").trim().length <= 18
    ));
    nodes.push(
      <ol
        className={`question-options exam-options ${hasImageOptions ? "image-options" : ""} ${compactOptions ? "compact-options" : ""}`}
        key={block.id}
      >
        {block.items.map((item) => (
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
              {item.imageUrl
                ? <img src={item.imageUrl} alt={`选项${item.label}`} loading="lazy" />
                : <span><InlineContent blocks={item.contentBlocks} /></span>}
            </button>
          </li>
        ))}
      </ol>,
    );
  });
  flushInline();
  return <div className="canonical-question-content">{nodes}</div>;
}
