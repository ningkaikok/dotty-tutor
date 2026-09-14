import { useEffect, useState } from "react";
import { createTutorThread, loadTutorThread, sendTutorMessage } from "../../api/tutoring";
import type { MistakeItem, SubQuestionAnswer, TutorStage, TutorThread } from "../../types/index";
import { buildStructuredAnswer } from "./structuredAnswer";

function hasMeaningfulValue(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(hasMeaningfulValue);
  if (value && typeof value === "object") return Object.values(value).some(hasMeaningfulValue);
  return typeof value === "boolean" || (value !== null && value !== undefined && String(value).trim().length > 0);
}

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
  const [subQuestionAnswers, setSubQuestionAnswers] = useState<Record<string, SubQuestionAnswer>>({});
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

  const submit = async (mode: "answer" | "help", inputId?: string) => {
    if (!thread || sending) return;
    const structured = buildStructuredAnswer(
      item.questionPayload.question, selectedOptions, blankAnswers, numericAnswer,
      drawConnections, subQuestionAnswers,
    );
    const interactionResult = structured.interactionResult;
    const hasStructuredAnswer = hasMeaningfulValue(interactionResult);
    const content = studentInput.trim() || structured.content;
    if (mode === "answer" && !content && !hasStructuredAnswer) {
      setError("请先输入或选择答案");
      return;
    }
    // Do not label an empty selection as a structured answer. This matters when
    // a learner asks a follow-up question after the original answer is cleared.
    const meaningfulInteractionResult = hasStructuredAnswer ? interactionResult : {};
    setSending(true);
    setError("");
    try {
      const result = await sendTutorMessage(thread.threadId, {
        content,
        mode,
        hintLevel: thread.hintLevel,
        ...(inputId ? { inputId } : {}),
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
      setSubQuestionAnswers({});
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
    subQuestionAnswers,
    loading,
    sending,
    error,
    setStudentInput,
    selectOption,
    setBlankAnswers,
    setNumericAnswer,
    setDrawConnections,
    setSubQuestionAnswers,
    setStage: (stage: TutorStage) => setThread((current) => current ? { ...current, stage } : current),
    submit,
  };
}
