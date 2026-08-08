import { useEffect, useState } from "react";
import { createTutorThread, loadTutorThread, sendTutorMessage } from "../../api";
import type { MistakeItem, TutorThread } from "../../types";

/**
 * Own the client-side state for one persisted tutoring thread.
 *
 * UI components only render controls. This hook restores the server thread,
 * translates each supported question control into the shared structured-answer
 * contract, and clears the draft only after a complete turn is persisted.
 */
export function useMistakeTutor(item: MistakeItem) {
  const [thread, setThread] = useState<TutorThread | null>(null);
  const [studentInput, setStudentInput] = useState("");
  const [selectedOptions, setSelectedOptions] = useState<string[]>([]);
  const [blankAnswers, setBlankAnswers] = useState<Record<string, string>>({});
  const [numericAnswer, setNumericAnswer] = useState("");
  const [drawConnections, setDrawConnections] = useState<Array<[string, string]>>([]);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    void createTutorThread(item.mistakeId)
      .then(async (created) => created.messages ? created : loadTutorThread(created.threadId))
      .then((loaded) => { if (!cancelled) setThread(loaded); })
      .catch((requestError) => {
        if (!cancelled) setError(requestError instanceof Error ? requestError.message : "辅导线程加载失败");
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [item.mistakeId]);

  const selectOption = (label: string, answerText: string) => {
    const multiple = item.questionPayload.question.questionType === "multi-select"
      || item.questionPayload.question.selectionMode === "multiple";
    const next = multiple
      ? selectedOptions.includes(label)
        ? selectedOptions.filter((option) => option !== label)
        : [...selectedOptions, label]
      : [label];
    setSelectedOptions(next);
    setStudentInput(`我选择${next.join("、")}${answerText && !multiple ? `：${answerText}` : ""}`);
  };

  const submit = async (mode: "answer" | "help") => {
    if (!thread || sending) return;
    const questionType = item.questionPayload.question.questionType;
    const interactionResult = questionType === "fill-blank"
      ? { blankAnswers }
      : questionType === "numeric"
        ? { numericAnswer }
        : questionType === "choice" || questionType === "multi-select" || questionType === "true-false"
          ? { selectedOptions }
          : questionType === "draw-line"
            ? { connections: drawConnections }
            : {};
    const hasStructuredAnswer = questionType === "fill-blank"
      ? Object.values(blankAnswers).some((answer) => answer.trim())
      : questionType === "numeric"
        ? Boolean(numericAnswer.trim())
        : questionType === "choice" || questionType === "multi-select" || questionType === "true-false"
          ? selectedOptions.length > 0
          : questionType === "draw-line"
            ? drawConnections.length > 0
            : false;
    const content = studentInput.trim()
      || (questionType === "fill-blank" ? Object.values(blankAnswers).join("；") : "")
      || (questionType === "numeric" ? numericAnswer : "")
      || (selectedOptions.length ? `我选择${selectedOptions.join("、")}` : "")
      || (drawConnections.length ? "我完成了画线作答" : "");
    if (mode === "answer" && !content && !hasStructuredAnswer) {
      setError("请先输入或选择答案");
      return;
    }
    setSending(true);
    setError("");
    try {
      const result = await sendTutorMessage(thread.threadId, {
        content,
        mode,
        hintLevel: thread.hintLevel,
        interactionResult,
      });
      setThread(result.thread);
      setStudentInput("");
      setSelectedOptions([]);
      setBlankAnswers({});
      setNumericAnswer("");
      setDrawConnections([]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "发送失败");
    } finally {
      setSending(false);
    }
  };

  return {
    thread,
    studentInput,
    selectedOptions,
    blankAnswers,
    numericAnswer,
    drawConnections,
    loading,
    sending,
    error,
    setStudentInput,
    selectOption,
    setBlankAnswers,
    setNumericAnswer,
    setDrawConnections,
    submit,
  };
}
