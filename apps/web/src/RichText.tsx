import type { ReactNode } from "react";
import MathText from "./MathText";
import { tokenizeRichText } from "./richTextParser";

/**
 * Render trusted rich text as plain text plus explicit math spans.
 *
 * This adapter intentionally is not a Markdown/HTML parser: ordinary content is
 * rendered as React text nodes, while only `$...$`/`$$...$$` fragments enter KaTeX.
 * That keeps lesson, tutor-history, and fallback messages on one safe path.
 */
export function RichText({ text, className }: { text: string; className?: string }) {
  const nodes: ReactNode[] = [];
  tokenizeRichText(text).forEach((token, index) => {
    if (token.kind === "math") {
      nodes.push(<MathText key={`math-${index}`} text={`${token.display ? "$$" : "$"}${token.value}${token.display ? "$$" : "$"}`} />);
    } else {
      nodes.push(...plainTextNodes(token.value, `plain-${index}`));
    }
  });
  return <span className={className}>{nodes}</span>;
}

function plainTextNodes(text: string, key: string): ReactNode[] {
  return text.split("\n").flatMap((line, index, lines) => [
    <span key={`${key}-${index}`}>{line}</span>,
    ...(index < lines.length - 1 ? [<br key={`${key}-break-${index}`} />] : []),
  ]);
}
