import { MathText } from "dotty-tutor";

const body = { fontFamily: "var(--font-bright)", fontSize: 18, color: "var(--c-ink)" };

export const InlineFormula = () => (
  <p style={body}>
    <MathText text="已知二次函数 $y = x^2 - 4x + 3$，求它的顶点坐标。" />
  </p>
);

export const DisplayFormula = () => (
  <div style={body}>
    <MathText text={"一元二次方程的求根公式：$$x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}$$"} />
  </div>
);

export const AsHeading = () => (
  <div style={{ ...body, fontWeight: 900 }}>
    <MathText block text={"第 3 题：化简 $\\sqrt{18} - \\sqrt{8}$"} />
  </div>
);
