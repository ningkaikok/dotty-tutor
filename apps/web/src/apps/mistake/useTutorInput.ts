import { useState } from "react";
import { createTutorInput, decideTutorObservation } from "../../api/tutoring";
import type { TutorCanvasState, TutorFormulaRecognition, TutorInput } from "../../types/tutoring";

/** Owns the pending multimodal envelope so the answer hook stays text/answer focused. */
export function useTutorInput(threadId: string) {
  const [input, setInput] = useState<TutorInput | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");

  const create = async (
    content: string,
    interactionResult: Record<string, unknown>,
    photo?: File,
    canvasState?: TutorCanvasState | null,
    formulaRecognitions?: TutorFormulaRecognition[],
  ) => {
    setSending(true);
    setError("");
    try {
      const saved = await createTutorInput(threadId, {
        content,
        interactionResult,
        photo,
        canvasState: canvasState ?? undefined,
        formulaRecognitions,
        mode: Object.keys(interactionResult).length || canvasState ? "structured" : "text",
      });
      setInput(saved);
      return saved;
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : "解题步骤上传失败";
      setError(message);
      throw requestError;
    } finally {
      setSending(false);
    }
  };

  const decide = async (decision: "confirm" | "correct" | "reject", correctedText = "") => {
    if (!input) return null;
    setSending(true);
    setError("");
    try {
      const saved = await decideTutorObservation(input.inputId, { decision, correctedText });
      setInput(saved);
      return saved;
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "识别结果保存失败");
      throw requestError;
    } finally {
      setSending(false);
    }
  };

  return { input, sending, error, create, decide, clear: () => setInput(null) };
}
