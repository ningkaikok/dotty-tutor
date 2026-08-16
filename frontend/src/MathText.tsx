import katex from "katex";
import "katex/dist/katex.min.css";

interface MathTextProps {
  text: string;
  className?: string;
  block?: boolean;
}

const INLINE_FRAGMENT = /(!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)|\$\$[\s\S]+?\$\$|\$[^$]+?\$)/g;
const RAW_MATH_COMMAND = /\\{1,2}(?:frac|sqrt|sum|int|text|mathrm|mathbf|mathbb|circ|times|div|leq|geq|left|right|begin|end)\b/i;

/**
 * 兼容已经持久化的旧公式。
 *
 * 后端修复只影响新生成内容，历史 lesson 仍可能包含审核模型写出的字面反斜杠命令。
 * 前端只修复已知命令和单位形式，不做通用 LaTeX 猜测，避免把合法公式改坏。
 */
function normalizeLegacyMath(expression: string) {
  return expression
    // 某些历史 JSON 把 LaTeX 反斜杠再次转义后直接存进了文本字段。
    // 只折叠已知数学命令，保留 `\\` 作为矩阵换行的合法写法。
    .replace(/\\\\(?=(?:frac|sqrt|sum|int|text|mathrm|mathbf|mathbb|circ|times|div|leq|geq|left|right|begin|end)\b)/gi, "\\")
    .replace(
      /\\textbackslash\s*\\text\s*\{\s*\}\s*\^+\s*(?:\\textcirc|\{\\circ\})\s*(?:\\mathrm\{C\}|C)?/gi,
      "^{\\circ}\\mathrm{C}",
    )
    .replace(/\\textbackslash\s*\\textcirc\s*C\b/gi, "^{\\circ}\\mathrm{C}")
    .replace(/\\textbackslash\s*\\text\s*\{\s*%\s*\}/gi, "\\%")
    .replace(/\\textbackslash\s*%/gi, "\\%")
    .replace(/\\(?:textdegree|textbar|textcirc)\s*C\b/gi, "^{\\circ}\\mathrm{C}");
}

function addMissingMathDelimiters(text: string) {
  // 新契约要求 `$...$`，但早期 OCR/模型结果可能只有裸 `\\frac{...}{...}`。
  // 仅在出现明确的数学命令时补定界符，并在中文标点处停止，避免把普通文本整段交给 KaTeX。
  if (text.includes("$") || !RAW_MATH_COMMAND.test(text)) return text;
  const match = RAW_MATH_COMMAND.exec(text);
  if (!match || match.index === undefined) return text;
  const prefix = text.slice(0, match.index);
  const remainder = text.slice(match.index).trim();
  const punctuation = remainder.search(/[，。；！？,.;!?]/);
  const expression = punctuation > 0 ? remainder.slice(0, punctuation).trim() : remainder;
  const suffix = punctuation > 0 ? remainder.slice(punctuation) : "";
  return `${prefix}$${expression}$${suffix}`;
}

function normalizeMathDelimiters(text: string) {
  // 模型有时会把 Markdown 定界符写成 `\$x\$`，或使用 LaTeX 的 `\(...\)`。
  // 这两种写法对 KaTeX 都是合法意图，但如果不先归一化会被 React 当普通文字显示。
  return text
    .replace(/\\+\$/g, "$")
    .replace(/\\\(([\s\S]*?)\\\)/g, "$$$1$")
    .replace(/\\\[([\s\S]*?)\\\]/g, "$$$$$1$$$$");
}

export default function MathText({ text, className, block = false }: MathTextProps) {
  const Tag = block ? "h2" : "span";
  const normalizedText = normalizeMathDelimiters(text);
  const source = addMissingMathDelimiters(normalizedText);
  const fragments: string[] = [];
  let cursor = 0;
  for (const match of source.matchAll(INLINE_FRAGMENT)) {
    const index = match.index ?? 0;
    if (index > cursor) fragments.push(source.slice(cursor, index));
    fragments.push(match[0]);
    cursor = index + match[0].length;
  }
  if (cursor < source.length) fragments.push(source.slice(cursor));

  return (
    <Tag className={className}>
      {fragments.map((fragment, index) => {
        const image = /^!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)$/.exec(fragment);
        if (image) {
          return (
            <img
              key={index}
              className="inline-content-image"
              src={image[2]}
              alt={image[1] || "题目图片"}
              loading="lazy"
            />
          );
        }
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
