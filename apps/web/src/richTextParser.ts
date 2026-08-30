export interface RichTextToken {
  kind: "text" | "math";
  value: string;
  display?: boolean;
}

function isEscaped(text: string, index: number): boolean {
  let slashes = 0;
  for (let cursor = index - 1; cursor >= 0 && text[cursor] === "\\"; cursor -= 1) slashes += 1;
  return slashes % 2 === 1;
}

function looksLikeMath(expression: string): boolean {
  const value = expression.trim();
  if (!value || /\r|\n/.test(value)) return false;
  // A prose connector or a currency-like decimal is ordinary text, not an
  // implicit formula. Explicit TeX markers and common operators remain valid.
  if (/\b(?:and|or|is|are|the)\b|(?:美元|人民币|元)/i.test(value)) return false;
  if (/^\d+(?:\.\d{1,2})?$/.test(value)) return false;
  return /[\\^_{}=+*/<>]|[A-Za-zα-ωΑ-Ω]/.test(value);
}

/** Split trusted copy into plain text and only explicitly delimited math. */
export function tokenizeRichText(input: string): RichTextToken[] {
  // RichText is deliberately not an HTML parser. If the source contains tags,
  // keep the whole value as text so a dollar inside a script/attribute cannot
  // accidentally create a KaTeX node.
  if (/<\/?[A-Za-z][^>]*>/.test(input)) {
    return [{ kind: "text", value: input.replace(/\\\$/g, "$") }];
  }
  const tokens: RichTextToken[] = [];
  let plain = "";
  const flushPlain = () => {
    if (plain) tokens.push({ kind: "text", value: plain.replace(/\\\$/g, "$") });
    plain = "";
  };

  for (let index = 0; index < input.length;) {
    if (input[index] !== "$" || isEscaped(input, index)) {
      plain += input[index];
      index += 1;
      continue;
    }
    const display = input.startsWith("$$", index);
    const delimiterLength = display ? 2 : 1;
    let close = -1;
    for (let cursor = index + delimiterLength; cursor < input.length; cursor += 1) {
      if (input[cursor] !== "$" || isEscaped(input, cursor)) continue;
      if (display ? input.startsWith("$$", cursor) : !input.startsWith("$$", cursor)) {
        close = cursor;
        break;
      }
    }
    if (close < 0) {
      plain += display ? "$$" : "$";
      index += delimiterLength;
      continue;
    }
    const expression = input.slice(index + delimiterLength, close);
    if (!display && !looksLikeMath(expression)) {
      plain += input.slice(index, close + 1);
      index = close + 1;
      continue;
    }
    flushPlain();
    tokens.push({ kind: "math", value: expression.trim(), display });
    index = close + delimiterLength;
  }
  flushPlain();
  return tokens.length ? tokens : [{ kind: "text", value: "" }];
}
