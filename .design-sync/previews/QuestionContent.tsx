import { QuestionContent } from "dotty-tutor";

// The answering page scopes its Bright option/prompt styles under .student-paper-shell;
// without that wrapper QuestionContent falls back to the legacy green exam styling.
const Shell = ({ children }: { children: React.ReactNode }) => (
  <div className="student-paper-shell" style={{ width: 640, boxSizing: "border-box", padding: 24, borderRadius: "var(--radius-xl)", background: "var(--c-surface)", fontSize: 20 }}>
    {children}
  </div>
);

const text = (id: string, value: string, sourceOrder: number) => ({ id, type: "text" as const, text: value, sourceOrder });
const math = (id: string, latex: string, sourceOrder: number, display = false) => ({ id, type: "math" as const, latex, display, sourceOrder });
const option = (label: string, latex: string) => ({ label, contentBlocks: [math(`opt-${label}`, latex, 0)] });

export const SingleChoice = () => (
  <Shell>
    <QuestionContent
      selectedOption="C"
      blocks={[
        text("t1", "下列各数中，是无理数的是（　　）", 1),
        { id: "o1", type: "options" as const, sourceOrder: 2, items: [option("A", "\\frac{1}{3}"), option("B", "\\sqrt{4}"), option("C", "\\pi"), { label: "D", contentBlocks: [text("opt-D", "0.25", 0)] }] },
      ]}
    />
  </Shell>
);

export const MultipleChoiceReadOnly = () => (
  <Shell>
    <QuestionContent
      multiple
      readOnly
      selectedOptions={["C"]}
      blocks={[
        text("t1", "关于一次函数 ", 1),
        math("m1", "y = -2x + 1", 2),
        text("t2", "，下列说法正确的是（　　）", 3),
        {
          id: "o1",
          type: "options" as const,
          sourceOrder: 4,
          items: [
            { label: "A", contentBlocks: [text("a", "图象经过第一、二、三象限", 0)] },
            { label: "B", contentBlocks: [text("b", "y 随 x 的增大而增大", 0)] },
            { label: "C", contentBlocks: [text("c", "图象与 y 轴交于点 (0, 1)", 0)] },
            { label: "D", contentBlocks: [text("d", "当 x > 0 时，y < 1", 0)] },
          ],
        },
      ]}
    />
  </Shell>
);

export const StemWithTable = () => (
  <Shell>
    <QuestionContent
      blocks={[
        text("t1", "某班 5 名同学的数学测验成绩如下表所示，求这组数据的平均数和中位数。", 1),
        {
          id: "tb1",
          type: "table" as const,
          sourceOrder: 2,
          rows: [
            { cells: ["同学", "甲", "乙", "丙", "丁", "戊"].map((v, i) => ({ contentBlocks: [text(`h${i}`, v, 0)] })) },
            { cells: ["成绩/分", "86", "92", "78", "95", "89"].map((v, i) => ({ contentBlocks: [text(`r${i}`, v, 0)] })) },
          ],
        },
      ]}
    />
  </Shell>
);

export const DisplayMathPrompt = () => (
  <Shell>
    <QuestionContent
      blocks={[
        text("t1", "解方程组：", 1),
        math("m1", "\\begin{cases} 2x + y = 7 \\\\ x - y = 2 \\end{cases}", 2, true),
      ]}
    />
  </Shell>
);
