import { useMemo, useState } from "react";
import type { QuestionInteraction } from "./types/index";

type Connection = [string, string];

interface DrawLineCanvasProps {
  interaction: QuestionInteraction;
  connections: Connection[];
  onChange: (connections: Connection[]) => void;
  readOnly?: boolean;
}

function normalizedPair(first: string, second: string): Connection {
  return [first, second].sort() as Connection;
}

export function DrawLineCanvas({ interaction, connections, onChange, readOnly = false }: DrawLineCanvasProps) {
  const [start, setStart] = useState<string | null>(null);
  const points = interaction.points;
  const pointById = useMemo(() => new Map(points.map((point) => [point.id, point])), [points]);

  const connect = (pointId: string) => {
    if (!start) {
      setStart(pointId);
      return;
    }
    if (start === pointId) {
      setStart(null);
      return;
    }
    const pair = normalizedPair(start, pointId);
    if (!connections.some((item) => item[0] === pair[0] && item[1] === pair[1])) {
      onChange([...connections, pair]);
    }
    setStart(null);
  };

  return (
    <div className="draw-line-workspace">
      <p className="draw-line-instruction">{interaction.instruction || "先点击一个端点，再点击另一个端点完成连线。"}</p>
      <svg className="draw-line-canvas" viewBox="0 0 100 100" role="img" aria-label="交互画线区域">
        <rect x="0" y="0" width="100" height="100" rx="4" className="draw-line-paper" />
        {connections.map(([first, second]) => {
          const from = pointById.get(first);
          const to = pointById.get(second);
          if (!from || !to) return null;
          return <line key={`${first}-${second}`} x1={from.x * 100} y1={from.y * 100} x2={to.x * 100} y2={to.y * 100} className="draw-line-created" />;
        })}
        {points.map((point) => (
          <g
            key={point.id}
            className={`draw-line-point ${start === point.id ? "active" : ""}`}
            data-testid={`draw-point-${point.id}`}
            onClick={readOnly ? undefined : () => connect(point.id)}
          >
            <circle cx={point.x * 100} cy={point.y * 100} r="4.4" />
            <text x={point.x * 100 + 5} y={point.y * 100 - 5}>{point.label}</text>
          </g>
        ))}
      </svg>
      <div className="draw-line-actions">
        <span>{start ? `已选 ${pointById.get(start)?.label ?? start}，请选择终点` : `已画 ${connections.length} 条线`}</span>
        <button type="button" className="ghost compact" disabled={readOnly} onClick={() => { onChange([]); setStart(null); }}>清除连线</button>
      </div>
    </div>
  );
}
