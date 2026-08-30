import { useEffect, useRef } from "react";
import { RichText } from "./RichText";
import type { CanvasAction } from "./types/index";

interface GeometryCanvasProps {
  action: CanvasAction;
  topic: string;
  title: string;
  text: string;
}

const COLORS = {
  ink: "#17251f",
  muted: "#8b968e",
  blue: "#4361ee",
  orange: "#dc7d26",
  mint: "#1b9a72",
  paper: "#fbfaf6",
};

export function GeometryCanvas({ action, topic, title, text }: GeometryCanvasProps) {
  const ref = useRef<HTMLCanvasElement>(null);
  const geometryMode = /几何|三角|垂直|轨迹|圆|角|线段/.test(topic);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;

    const ratio = window.devicePixelRatio || 1;
    const box = canvas.getBoundingClientRect();
    canvas.width = Math.round(box.width * ratio);
    canvas.height = Math.round(box.height * ratio);
    context.scale(ratio, ratio);

    const w = box.width;
    const h = box.height;
    context.clearRect(0, 0, w, h);
    context.fillStyle = COLORS.paper;
    context.fillRect(0, 0, w, h);

    if (!geometryMode) {
      const stepIndex = CANVAS_STEP[action];
      const padding = Math.max(28, w * 0.07);
      context.fillStyle = "#e7f1ec";
      context.fillRect(padding, 42, w - padding * 2, 5);
      context.fillStyle = COLORS.mint;
      context.fillRect(padding, 42, (w - padding * 2) * ((stepIndex + 1) / 4), 5);

      context.fillStyle = COLORS.mint;
      context.font = "700 13px Inter, system-ui, sans-serif";
      context.fillText(`STEP ${stepIndex + 1} / 4`, padding, 82);
      context.fillStyle = COLORS.ink;
      context.font = `700 ${Math.min(30, Math.max(22, w / 22))}px Inter, system-ui, sans-serif`;
      drawWrappedText(context, title, padding, 125, w - padding * 2, 40, 2);
      context.fillStyle = "#eff0eb";
      context.fillRect(padding, h - 58, w - padding * 2, 30);
      context.fillStyle = "#748078";
      context.font = "600 12px Inter, system-ui, sans-serif";
      context.fillText(topic, padding + 12, h - 38);
      return;
    }

    const A = { x: w * 0.2, y: h * 0.68 };
    const B = { x: w * 0.8, y: h * 0.68 };
    const M = { x: w * 0.5, y: h * 0.68 };
    const P = { x: w * 0.5, y: h * 0.2 };

    const line = (
      from: { x: number; y: number },
      to: { x: number; y: number },
      color = COLORS.muted,
      width = 2,
      dash: number[] = [],
    ) => {
      context.beginPath();
      context.setLineDash(dash);
      context.moveTo(from.x, from.y);
      context.lineTo(to.x, to.y);
      context.strokeStyle = color;
      context.lineWidth = width;
      context.stroke();
      context.setLineDash([]);
    };

    const point = (position: { x: number; y: number }, label: string, color = COLORS.orange) => {
      context.beginPath();
      context.arc(position.x, position.y, 5, 0, Math.PI * 2);
      context.fillStyle = color;
      context.fill();
      context.font = "600 15px Inter, system-ui, sans-serif";
      context.fillStyle = COLORS.ink;
      context.fillText(label, position.x + 9, position.y - 9);
    };

    line(A, B, COLORS.muted, 2, [6, 6]);
    point(A, "A");
    point(B, "B");
    point(M, "M", COLORS.ink);

    if (action !== "show-base") {
      line(P, A, COLORS.blue, 2.5);
      line(P, B, COLORS.blue, 2.5);
      point(P, "P", COLORS.blue);
    }

    if (action === "show-triangles" || action === "show-bisector") {
      context.beginPath();
      context.moveTo(P.x, P.y);
      context.lineTo(A.x, A.y);
      context.lineTo(B.x, B.y);
      context.closePath();
      context.fillStyle = "rgba(67, 97, 238, 0.07)";
      context.fill();
      line(P, M, COLORS.mint, 4);

      context.strokeStyle = COLORS.ink;
      context.lineWidth = 1.5;
      context.strokeRect(M.x, M.y - 12, 12, 12);
    }

    if (action === "show-bisector") {
      line({ x: M.x, y: h * 0.06 }, { x: M.x, y: h * 0.92 }, COLORS.mint, 2, [7, 6]);
      context.fillStyle = COLORS.mint;
      context.font = "600 13px Inter, system-ui, sans-serif";
      context.fillText("P 的运动轨迹", M.x + 12, h * 0.11);
    }
  }, [action, geometryMode, text, title, topic]);

  return (
    <div className="geometry-canvas-wrap">
      <canvas ref={ref} className="geometry-canvas" aria-label="动态讲解画板" />
      {!geometryMode && (
        <div className="geometry-canvas-text" aria-label="画布讲解文字">
          <RichText text={text} />
        </div>
      )}
    </div>
  );
}

const CANVAS_STEP: Record<CanvasAction, number> = {
  "show-base": 0,
  "show-point-p": 1,
  "show-triangles": 2,
  "show-bisector": 3,
};

function drawWrappedText(
  context: CanvasRenderingContext2D,
  value: string,
  x: number,
  y: number,
  maxWidth: number,
  lineHeight: number,
  maxLines: number,
) {
  const characters = Array.from(value);
  let line = "";
  let lineIndex = 0;
  for (const character of characters) {
    const candidate = line + character;
    if (context.measureText(candidate).width > maxWidth && line) {
      context.fillText(line, x, y + lineIndex * lineHeight);
      line = character;
      lineIndex += 1;
      if (lineIndex >= maxLines) return;
    } else {
      line = candidate;
    }
  }
  if (line && lineIndex < maxLines) context.fillText(line, x, y + lineIndex * lineHeight);
}
