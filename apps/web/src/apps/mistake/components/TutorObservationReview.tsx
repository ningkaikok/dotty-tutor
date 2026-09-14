import { useState } from "react";
import type { TutorInput } from "../../../types/tutoring";

export function TutorObservationReview({
  input, disabled, onDecision,
}: { input: TutorInput; disabled?: boolean; onDecision: (decision: "confirm" | "correct" | "reject", correctedText?: string) => void }) {
  const [correction, setCorrection] = useState(input.observation?.recognizedText ?? "");
  const observation = input.observation;
  if (!observation) return null;
  return (
    <div className="tutor-observation-review" role="status">
      <strong>请确认照片识别结果</strong>
      <p>识别置信度：{Math.round(observation.confidence * 100)}%。照片观察只作为待确认证据，不会直接判题。</p>
      <label>识别文字与公式
        <textarea value={correction} onChange={(event) => setCorrection(event.target.value)} disabled={disabled} />
      </label>
      {observation.evidenceRegions.length > 0 && <small>已标记 {observation.evidenceRegions.length} 个证据区域</small>}
      <div className="tutor-actions">
        <button disabled={disabled} onClick={() => onDecision("reject")}>拒绝这次识别</button>
        <button disabled={disabled} onClick={() => onDecision("correct", correction)}>修正并确认</button>
        <button className="mistake-primary-action compact" disabled={disabled} onClick={() => onDecision("confirm")}>确认识别</button>
      </div>
    </div>
  );
}
