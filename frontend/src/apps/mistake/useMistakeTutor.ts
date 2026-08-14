import { useEffect, useState } from "react";
import { createTutorThread, loadTutorThread, sendTutorMessage } from "../../api";
import type { MistakeItem, TutorStage, TutorThread } from "../../types";

/**
 * 管理一个持久化陪练线程的客户端状态。
 *
 * UI 组件只渲染控件；本 Hook 恢复服务端线程，把不同题型转换为共享结构化答案，
 * 并且只在一轮对话完整写入后清空学生草稿。
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
    // API 对“错题 + 学生”幂等，因此先调用 create 既能创建新线程，也能恢复已有线程。
    // cancelled 阻止慢响应更新已经卸载或切换到其他错题的页面。
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
    // 所有可视化作答控件都转换为后端共享结构；自由文本继续承载思路，
    // 但确定性判题优先使用无歧义的结构化字段。
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
    // Keep the request shape backward compatible, but do not label an empty
    // selection as a structured answer.  This matters when a learner asks a
    // follow-up question after the original answer has been cleared.
    const meaningfulInteractionResult = hasStructuredAnswer ? interactionResult : {};
    setSending(true);
    setError("");
    try {
      const result = await sendTutorMessage(thread.threadId, {
        content,
        mode,
        hintLevel: thread.hintLevel,
        ...(Object.keys(meaningfulInteractionResult).length > 0
          ? { interactionResult: meaningfulInteractionResult }
          : {}),
      });
      // 服务端原子保存学生与助教两侧消息后才清空草稿；请求失败时保留现场，允许原样重试。
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
    setStage: (stage: TutorStage) => setThread((current) => current ? { ...current, stage } : current),
    submit,
  };
}
