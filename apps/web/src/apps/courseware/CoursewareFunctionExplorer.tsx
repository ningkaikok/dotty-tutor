import { useState } from "react";
import { InteractiveMathCanvas } from "../../InteractiveMathCanvas";
import type { TutorCanvasState } from "../../types/tutoring";

function bounded(value: unknown, fallback: number) {
  const number = typeof value === "number" || (typeof value === "string" && value.trim()) ? Number(value) : NaN;
  return Number.isFinite(number) ? Math.max(-5, Math.min(5, Math.round(number))) : fallback;
}

function coordinate(value: string | undefined, fallback: number) {
  const number = value?.trim() ? Number(value) : NaN;
  return Number.isFinite(number) ? Math.max(-5, Math.min(5, number)) : fallback;
}

/** Parameters and pointer positions are local exploration; only explicit practice submission is graded. */
export function CoursewareFunctionExplorer({ schema, values, onChange }: {
  schema: Record<string, string | number | boolean>;
  values: string[];
  onChange: (index: number, value: string) => void;
}) {
  const [staticOnly, setStaticOnly] = useState(false);
  const slope = bounded(values[1], bounded(schema.slope, 1));
  const intercept = bounded(values[2], bounded(schema.intercept, 0));
  const sampleX = bounded(schema.sampleX, 2);
  const sampleY = slope * sampleX + intercept;
  const pointX = coordinate(values[3], sampleX);
  const pointY = coordinate(values[4], Math.max(-5, Math.min(5, sampleY)));
  const canvas: TutorCanvasState = { schemaVersion: "math-canvas-v1", kind: "point-placement",
    xMin: -5, xMax: 5, yMin: -5, yMax: 5, points: [{ id: "student", x: pointX, y: pointY }], operations: [] };
  return <section className="courseware-function-explorer" aria-label="一次函数参数探索">
    <h3>一次函数参数探索</h3>
    <p>y = {slope}x {intercept < 0 ? "−" : "+"} {Math.abs(intercept)}</p>
    <p>x = {sampleX} 时，y = {sampleY}</p>
    <p>先按活动说明设置参数；移动点和调整参数不会提交作答。</p>
    <button type="button" onClick={() => setStaticOnly((value) => !value)}>{staticOnly ? "恢复交互探索" : "只看静态说明"}</button>
    {!staticOnly && <>
      <label>斜率 k<input aria-label="斜率 k" type="range" min="-5" max="5" step="1" value={slope} onChange={(event) => onChange(1, event.target.value)} /></label>
      <label>截距 b<input aria-label="截距 b" type="range" min="-5" max="5" step="1" value={intercept} onChange={(event) => onChange(2, event.target.value)} /></label>
      <InteractiveMathCanvas value={canvas} guideLine={{ slope, intercept }} onChange={(next) => {
        const point = next.points[0];
        if (point) { onChange(3, String(point.x)); onChange(4, String(point.y)); }
      }} />
      <button type="button" onClick={() => onChange(0, String(sampleY))}>把当前函数值填入作答</button>
    </>}
    <table aria-label="函数取值表"><thead><tr><th>x</th>{[-2, -1, 0, 1, 2].map((x) => <th key={x}>{x}</th>)}</tr></thead>
      <tbody><tr><th>y</th>{[-2, -1, 0, 1, 2].map((x) => <td key={x}>{slope * x + intercept}</td>)}</tr></tbody></table>
    <p>斜率每增加 1，x 每增加 1 时的 y 增量也增加 1；截距表示 x=0 时的 y。超出坐标范围的部分可用取值表核对。</p>
  </section>;
}
