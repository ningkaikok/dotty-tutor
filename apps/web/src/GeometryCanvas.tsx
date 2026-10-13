import { useEffect, useRef } from "react";
import { RichText } from "./RichText";
import type { CanvasAction } from "./types/index";

interface GeometryCanvasProps {
  action: CanvasAction;
  topic: string;
  title: string;
  text: string;
}

/**
 * Canvas 不能直接引用 CSS 变量，绘制时从元素的计算样式读取 Bright token；
 * 回退值与 tokens.css 一致，保证样式表未加载（如测试环境）时仍能画出来。
 */
const TOKENS = {
  ink: ["--c-ink", "#1c1442"],
  muted: ["--c-ink-muted", "#5a5280"],
  brand: ["--c-brand", "#3a2bc4"],
  brandSoft: ["--c-brand-soft", "#eeeafd"],
  accent: ["--c-accent", "#ff9f1c"],
  locus: ["--c-on-success-soft", "#0b5537"],
  paper: ["--c-surface-tint", "#f7f6fe"],
  ground: ["--c-ground", "#f1effc"],
  font: ["--font-bright", "system-ui, sans-serif"],
} as const;

type Palette = Record<keyof typeof TOKENS, string>;

function readPalette(element: Element): Palette {
  const style = getComputedStyle(element);
  return Object.fromEntries(Object.entries(TOKENS).map(([name, [token, fallback]]) => [
    name, style.getPropertyValue(token).trim() || fallback,
  ])) as Palette;
}

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
    const colors = readPalette(canvas);
    const font = (weight: number, size: number) => `${weight} ${size}px ${colors.font}`;
    context.clearRect(0, 0, w, h);
    context.fillStyle = colors.paper;
    context.fillRect(0, 0, w, h);

    if (!geometryMode) {
      const stepIndex = CANVAS_STEP[action];
      const padding = Math.max(28, w * 0.07);
      context.fillStyle = colors.brandSoft;
      context.fillRect(padding, 42, w - padding * 2, 5);
      context.fillStyle = colors.brand;
      context.fillRect(padding, 42, (w - padding * 2) * ((stepIndex + 1) / 4), 5);

      context.fillStyle = colors.brand;
      context.font = font(800, 13);
      context.fillText(`STEP ${stepIndex + 1} / 4`, padding, 82);
      context.fillStyle = colors.ink;
      context.font = font(900, Math.min(30, Math.max(22, w / 22)));
      drawWrappedText(context, title, padding, 125, w - padding * 2, 40, 2);
      context.fillStyle = colors.ground;
      context.fillRect(padding, h - 58, w - padding * 2, 30);
      context.fillStyle = colors.muted;
      context.font = font(700, 12);
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
      color = colors.muted,
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

    const point = (position: { x: number; y: number }, label: string, color = colors.accent) => {
      context.beginPath();
      context.arc(position.x, position.y, 5, 0, Math.PI * 2);
      context.fillStyle = color;
      context.fill();
      context.font = font(700, 15);
      context.fillStyle = colors.ink;
      context.fillText(label, position.x + 9, position.y - 9);
    };

    line(A, B, colors.muted, 2, [6, 6]);
    point(A, "A");
    point(B, "B");
    point(M, "M", colors.ink);

    if (action !== "show-base") {
      line(P, A, colors.brand, 2.5);
      line(P, B, colors.brand, 2.5);
      point(P, "P", colors.brand);
    }

    if (action === "show-triangles" || action === "show-bisector") {
      context.beginPath();
      context.moveTo(P.x, P.y);
      context.lineTo(A.x, A.y);
      context.lineTo(B.x, B.y);
      context.closePath();
      context.globalAlpha = 0.08;
      context.fillStyle = colors.brand;
      context.fill();
      context.globalAlpha = 1;
      line(P, M, colors.locus, 4);

      context.strokeStyle = colors.ink;
      context.lineWidth = 1.5;
      context.strokeRect(M.x, M.y - 12, 12, 12);
    }

    if (action === "show-bisector") {
      line({ x: M.x, y: h * 0.06 }, { x: M.x, y: h * 0.92 }, colors.locus, 2, [7, 6]);
      context.fillStyle = colors.locus;
      context.font = font(700, 13);
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
