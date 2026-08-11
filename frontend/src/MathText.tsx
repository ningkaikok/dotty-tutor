import katex from "katex";
import "katex/dist/katex.min.css";

interface MathTextProps {
  text: string;
  className?: string;
  block?: boolean;
}

const MATH_FRAGMENT = /(\$\$[\s\S]+?\$\$|\$[^$]+?\$)/g;

/**
 * 兼容已经持久化的旧公式。
 *
 * 后端修复只影响新生成内容，历史 lesson 仍可能包含审核模型写出的字面反斜杠命令。
 * 前端仅修复三种已知单位形式，不做通用 LaTeX 猜测，避免把合法公式改坏。
 */
function normalizeLegacyMath(expression: string) {
  return expression
    .replace(
      /\\textbackslash\s*\\text\s*\{\s*\}\s*\^+\s*(?:\\textcirc|\{\\circ\})\s*(?:\\mathrm\{C\}|C)?/gi,
      "^{\\circ}\\mathrm{C}",
    )
    .replace(/\\textbackslash\s*\\textcirc\s*C\b/gi, "^{\\circ}\\mathrm{C}")
    .replace(/\\textbackslash\s*\\text\s*\{\s*%\s*\}/gi, "\\%")
    .replace(/\\textbackslash\s*%/gi, "\\%")
    .replace(/\\(?:textdegree|textbar|textcirc)\s*C\b/gi, "^{\\circ}\\mathrm{C}");
}

export default function MathText({ text, className, block = false }: MathTextProps) {
  const Tag = block ? "h2" : "span";
  const fragments = text.split(MATH_FRAGMENT).filter(Boolean);

  return (
    <Tag className={className}>
      {fragments.map((fragment, index) => {
        const displayMode = fragment.startsWith("$$");
        const isMath = displayMode || (fragment.startsWith("$") && fragment.endsWith("$"));
        if (!isMath) return <span key={index}>{fragment}</span>;
        const expression = normalizeLegacyMath(
          fragment.slice(displayMode ? 2 : 1, displayMode ? -2 : -1).trim(),
        );
        return (
          <span
            key={index}
            className={displayMode ? "math-display" : "math-inline"}
            // KaTeX 输出的是本地生成 HTML；trust=false 禁止模型公式启用 URL、HTML 等信任命令。
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
