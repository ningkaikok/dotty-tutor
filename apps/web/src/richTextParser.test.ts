import { describe, expect, it } from "vitest";
import { tokenizeRichText } from "./richTextParser";

describe("tokenizeRichText", () => {
  it("keeps ordinary prose, newlines and money out of math", () => {
    expect(tokenizeRichText("普通文本\n第二行，价格 $5.00，另一个 $10。"))
      .toEqual([{ kind: "text", value: "普通文本\n第二行，价格 $5.00，另一个 $10。" }]);
  });

  it("recognizes only explicit inline and display formulas", () => {
    expect(tokenizeRichText("求 $x^2+1$\n$$y=2x$$")).toEqual([
      { kind: "text", value: "求 " },
      { kind: "math", value: "x^2+1", display: false },
      { kind: "text", value: "\n" },
      { kind: "math", value: "y=2x", display: true },
    ]);
  });

  it("handles escaped and unclosed delimiters as text", () => {
    expect(tokenizeRichText("金额 \\$5，未闭合 $x^2，字面 \\$y\\$"))
      .toEqual([{ kind: "text", value: "金额 $5，未闭合 $x^2，字面 $y$" }]);
  });

  it("does not turn html or script into markup", () => {
    expect(tokenizeRichText("<script>alert('$x$')</script>")).toEqual([
      { kind: "text", value: "<script>alert('$x$')</script>" },
    ]);
  });
});
