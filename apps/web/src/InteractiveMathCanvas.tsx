import { useCallback, useMemo, type PointerEvent } from "react";
import type { TutorCanvasState } from "./types/tutoring";

interface InteractiveMathCanvasProps {
  value: TutorCanvasState;
  onChange: (value: TutorCanvasState) => void;
  readOnly?: boolean;
}

const WIDTH = 100;
const HEIGHT = 100;

function toCanvasX(x: number, state: TutorCanvasState) {
  return ((x - state.xMin) / (state.xMax - state.xMin)) * WIDTH;
}

function toCanvasY(y: number, state: TutorCanvasState) {
  return HEIGHT - ((y - state.yMin) / (state.yMax - state.yMin)) * HEIGHT;
}

export function InteractiveMathCanvas({ value, onChange, readOnly = false }: InteractiveMathCanvasProps) {
  const point = value.points.find((item) => item.id === "student") ?? value.points[0];
  const gridLines = useMemo(() => Array.from({ length: 9 }, (_, index) => (index + 1) * 10), []);

  const movePoint = useCallback((event: PointerEvent<SVGSVGElement>) => {
    if (readOnly) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const px = Math.max(0, Math.min(WIDTH, ((event.clientX - rect.left) / rect.width) * WIDTH));
    const py = Math.max(0, Math.min(HEIGHT, ((event.clientY - rect.top) / rect.height) * HEIGHT));
    const x = value.xMin + (px / WIDTH) * (value.xMax - value.xMin);
    const y = value.yMax - (py / HEIGHT) * (value.yMax - value.yMin);
    const nextPoint = { id: "student", x: Number(x.toFixed(2)), y: Number(y.toFixed(2)) };
    onChange({
      ...value,
      points: [nextPoint],
      operations: [...value.operations, { type: "move-point", pointId: "student", x: nextPoint.x, y: nextPoint.y }].slice(-200),
    });
  }, [onChange, readOnly, value]);

  const moveByKeyboard = (deltaX: number, deltaY: number) => {
    if (readOnly || !point) return;
    const x = Math.max(value.xMin, Math.min(value.xMax, point.x + deltaX));
    const y = Math.max(value.yMin, Math.min(value.yMax, point.y + deltaY));
    onChange({
      ...value,
      points: [{ id: "student", x, y }],
      operations: [...value.operations, { type: "move-point", pointId: "student", x, y, source: "keyboard" }].slice(-200),
    });
  };

  return (
    <div className="interactive-math-canvas">
      <p>{"在坐标系中移动点；画布会保存结构化坐标，不用截图判题。"}</p>
      <svg
        viewBox="0 0 100 100"
        role="application"
        aria-label="交互数学画布"
        aria-roledescription="可操作坐标画布"
        aria-valuetext={point ? `当前点 (${point.x}, ${point.y})` : "尚未设置点"}
        aria-disabled={readOnly}
        tabIndex={0}
        onPointerMove={(event) => { if (event.buttons === 1) movePoint(event); }}
        onPointerDown={movePoint}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") { event.preventDefault(); moveByKeyboard(-0.25, 0); }
          if (event.key === "ArrowRight") { event.preventDefault(); moveByKeyboard(0.25, 0); }
          if (event.key === "ArrowUp") { event.preventDefault(); moveByKeyboard(0, 0.25); }
          if (event.key === "ArrowDown") { event.preventDefault(); moveByKeyboard(0, -0.25); }
          if (!readOnly && (event.key === "Enter" || event.key === " ")) {
            event.preventDefault();
            // Enter/Space confirms the focused point without requiring a pointer.
            moveByKeyboard(0, 0);
          }
        }}
      >
        <rect x="0" y="0" width="100" height="100" className="math-canvas-paper" />
        {gridLines.map((position) => <line key={`v-${position}`} x1={position} y1="0" x2={position} y2="100" className="math-canvas-grid" />)}
        {gridLines.map((position) => <line key={`h-${position}`} x1="0" y1={position} x2="100" y2={position} className="math-canvas-grid" />)}
        <line x1={toCanvasX(0, value)} y1="0" x2={toCanvasX(0, value)} y2="100" className="math-canvas-axis" />
        <line x1="0" y1={toCanvasY(0, value)} x2="100" y2={toCanvasY(0, value)} className="math-canvas-axis" />
        {point && <circle cx={toCanvasX(point.x, value)} cy={toCanvasY(point.y, value)} r="3.8" className="math-canvas-student-point" />}
      </svg>
      <div className="math-canvas-controls">
        <span>{point ? `当前点：(${point.x}, ${point.y})` : "拖动画布放置点"}</span>
        <button type="button" className="ghost compact" onClick={() => moveByKeyboard(-0.25, 0)} disabled={readOnly}>←</button>
        <button type="button" className="ghost compact" onClick={() => moveByKeyboard(0.25, 0)} disabled={readOnly}>→</button>
        <button type="button" className="ghost compact" onClick={() => moveByKeyboard(0, 0.25)} disabled={readOnly}>↑</button>
        <button type="button" className="ghost compact" onClick={() => moveByKeyboard(0, -0.25)} disabled={readOnly}>↓</button>
      </div>
    </div>
  );
}
