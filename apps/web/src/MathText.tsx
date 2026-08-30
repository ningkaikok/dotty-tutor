import { createElement, type ReactNode } from "react";
import katex from "katex";
import "katex/dist/katex.min.css";
import { tokenizeRichText } from "./richTextParser";

interface MathTextProps {
  text: string;
  className?: string;
  block?: boolean;
}

/** Render the current text contract: ordinary text plus explicit `$...$` math. */
export default function MathText({ text, className, block = false }: MathTextProps) {
  const Tag = block ? "h2" : "span";
  const fragments = tokenizeRichText(text);

  return (
    <Tag className={className}>
      {fragments.map((fragment, index) => {
        if (fragment.kind === "text") return <span key={index}>{fragment.value}</span>;
        return <span key={index} className={fragment.display ? "math-display" : "math-inline"}>{renderKatex(fragment.value, Boolean(fragment.display))}</span>;
      })}
    </Tag>
  );
}

function renderKatex(expression: string, displayMode: boolean): ReactNode {
  const html = katex.renderToString(expression, {
    displayMode,
    throwOnError: false,
    strict: "ignore",
    trust: false,
  });
  // KaTeX returns a fixed, locally generated DOM tree. Convert that tree to React
  // elements instead of accepting arbitrary HTML from lesson/tutor text.
  if (typeof DOMParser === "undefined") return expression;
  const parsed = new DOMParser().parseFromString(`<span>${html}</span>`, "text/html");
  const root = parsed.body.firstElementChild;
  return root ? Array.from(root.childNodes).map((node, index) => nodeToReact(node, index)) : expression;
}

function nodeToReact(node: ChildNode, key: number): ReactNode {
  if (node.nodeType === 3) return node.textContent;
  if (node.nodeType !== 1) return null;
  const element = node as Element;
  const props: Record<string, unknown> = { key };
  for (const attribute of Array.from(element.attributes)) {
    if (attribute.name === "class") props.className = attribute.value;
    else if (attribute.name === "style") props.style = parseStyle(attribute.value);
    else props[attribute.name] = attribute.value;
  }
  return createElement(
    element.tagName.toLowerCase(),
    props,
    ...Array.from(element.childNodes).map((child, index) => nodeToReact(child, index)),
  );
}

function parseStyle(value: string): Record<string, string> {
  return Object.fromEntries(value.split(";").flatMap((declaration) => {
    const [property, ...rest] = declaration.split(":");
    if (!property || !rest.length) return [];
    const camel = property.trim().replace(/-([a-z])/g, (_match, letter: string) => letter.toUpperCase());
    return [[camel, rest.join(":").trim()]];
  }));
}
