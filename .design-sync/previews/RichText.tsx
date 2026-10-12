import { RichText } from "dotty-tutor";

const bubble = {
  maxWidth: 560,
  margin: 0,
  padding: "12px 16px",
  borderRadius: "4px 18px 18px 18px",
  background: "var(--c-brand-soft)",
  color: "var(--c-ink)",
  fontFamily: "var(--font-bright)",
  fontSize: 16,
  lineHeight: 1.7,
};

export const TutorExplanation = () => (
  <p style={bubble}>
    <RichText text={"先把方程整理成一般式 $ax^2 + bx + c = 0$。\n这里 $a = 1$，$b = -5$，$c = 6$，所以判别式 $\\Delta = b^2 - 4ac = 1 > 0$。\n方程有两个不相等的实数根。"} />
  </p>
);

export const WithDisplayMath = () => (
  <p style={bubble}>
    <RichText text={"勾股定理告诉我们，直角三角形两直角边的平方和等于斜边的平方：$$a^2 + b^2 = c^2$$当 $a = 3$，$b = 4$ 时，斜边 $c = 5$。"} />
  </p>
);
