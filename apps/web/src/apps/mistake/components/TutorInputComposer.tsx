import { useRef, useState } from "react";
import { InteractiveMathCanvas } from "../../../InteractiveMathCanvas";
import type { TutorCanvasState } from "../../../types/tutoring";

const DEFAULT_CANVAS: TutorCanvasState = {
  schemaVersion: "math-canvas-v1",
  kind: "point-placement",
  xMin: -5,
  xMax: 5,
  yMin: -5,
  yMax: 5,
  points: [{ id: "student", x: 0, y: 0 }],
  operations: [],
};

export function TutorInputComposer({
  value, disabled, onChange, onPhoto, canvasState, onCanvasChange,
}: {
  value: string;
  disabled?: boolean;
  onChange: (value: string) => void;
  onPhoto: (file: File) => void;
  canvasState?: TutorCanvasState | null;
  onCanvasChange: (value: TutorCanvasState | null) => void;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [showCanvas, setShowCanvas] = useState(Boolean(canvasState));
  const activeCanvas = canvasState ?? DEFAULT_CANVAS;
  return (
    <div className="tutor-input-composer">
      <label className="tutor-input">
        <span>继续回答或描述你的想法</span>
        <textarea value={value} onChange={(event) => onChange(event.target.value)} placeholder="例如：我觉得要先比较这些数和 1 的大小……" disabled={disabled} />
      </label>
      <div className="tutor-photo-action">
        <input ref={fileInput} type="file" accept="image/jpeg,image/png,image/webp" hidden onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onPhoto(file);
          event.target.value = "";
        }} />
        <button type="button" disabled={disabled} onClick={() => fileInput.current?.click()}>附加一张步骤照片</button>
        <button
          type="button"
          className="tutor-canvas-toggle"
          disabled={disabled}
          onClick={() => {
            const next = !showCanvas;
            setShowCanvas(next);
            onCanvasChange(next ? activeCanvas : null);
          }}
        >{showCanvas ? "收起数学画布" : "打开数学画布"}</button>
      </div>
      {showCanvas && <InteractiveMathCanvas value={activeCanvas} onChange={onCanvasChange} readOnly={disabled} />}
    </div>
  );
}
