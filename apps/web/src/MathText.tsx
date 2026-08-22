import katex from "katex";
import "katex/dist/katex.min.css";

interface MathTextProps {
  text: string;
  className?: string;
  block?: boolean;
}

const MATH_FRAGMENT = /(\$\$[\s\S]+?\$\$|\$[^$]+?\$)/g;

/** Render the current text contract: ordinary text plus explicit `$...$` math. */
export default function MathText({ text, className, block = false }: MathTextProps) {
  const Tag = block ? "h2" : "span";
  const fragments: string[] = [];
  let cursor = 0;
  for (const match of text.matchAll(MATH_FRAGMENT)) {
    const index = match.index ?? 0;
    if (index > cursor) fragments.push(text.slice(cursor, index));
    fragments.push(match[0]);
    cursor = index + match[0].length;
  }
  if (cursor < text.length) fragments.push(text.slice(cursor));

  return (
    <Tag className={className}>
      {fragments.map((fragment, index) => {
        const displayMode = fragment.startsWith("$$");
        const isMath = displayMode || (fragment.startsWith("$") && fragment.endsWith("$"));
        if (!isMath) return <span key={index}>{fragment}</span>;
        const expression = fragment.slice(displayMode ? 2 : 1, displayMode ? -2 : -1).trim();
        return (
          <span
            key={index}
            className={displayMode ? "math-display" : "math-inline"}
            dangerouslySetInnerHTML={{
              __html: katex.renderToString(expression, {
                displayMode,
                throwOnError: false,
                strict: "ignore",
                trust: false,
              }),
            }}
          />
        );
      })}
    </Tag>
  );
}
