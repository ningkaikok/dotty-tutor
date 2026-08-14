import { useEffect, useRef, useState } from "react";
import { answerVariation, createVariation, listVariations } from "../../api";
import type { VariationExercise } from "../../types";
import type { TutorStage } from "../../types";
import { buildStructuredAnswer } from "./structuredAnswer";

/** 管理一次可计分的变式作答，不把掌握证据混入自由对话消息。 */
export function useVariationPractice(
  mistakeId: string,
  autoStart = false,
  onStageChange?: (stage: TutorStage) => void,
) {
  const [items, setItems] = useState<VariationExercise[]>([]);
  const [selectedOptions, setSelectedOptions] = useState<string[]>([]);
  const [blankAnswers, setBlankAnswers] = useState<Record<string, string>>({});
  const [numericAnswer, setNumericAnswer] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const autoStarted = useRef(false);
  const active = items.length ? items[items.length - 1] : null;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void listVariations(mistakeId)
      .then((loaded) => { if (!cancelled) setItems(loaded); })
      .catch((requestError) => {
        if (!cancelled) setError(requestError instanceof Error ? requestError.message : "验证题加载失败");
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [mistakeId]);

  // 进入 verify 就代表学生已确认可以继续；第一道验证题直接开始生成，
  // 不再要求学生在对话区和验证区之间寻找第二个“下一题”按钮。已有记录
  // 会被复用，因此刷新页面不会重复创建题目。
  const resetDraft = () => {
    setSelectedOptions([]);
    setBlankAnswers({});
    setNumericAnswer("");
  };

  const generate = async () => {
    // 一道验证题贯穿整个验证阶段；已有题目只从后端恢复，不再生成下一道。
    if (submitting || active) return;
    setSubmitting(true);
    setError("");
    try {
      const created = await createVariation(mistakeId);
      setItems((current) => [...current, created]);
      resetDraft();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "验证题生成失败");
    } finally {
      setSubmitting(false);
    }
  };

  useEffect(() => {
    if (!autoStart || loading || items.length > 0 || autoStarted.current || submitting) return;
    autoStarted.current = true;
    void generate();
  }, [autoStart, items.length, loading, submitting]);

  const selectOption = (label: string) => {
    if (!active || (active.status === "answered" && active.assessment === "correct")) return;
    const question = active.questionPayload.question;
    const multiple = question.questionType === "multi-select" || question.selectionMode === "multiple";
    setSelectedOptions((current) => multiple
      ? current.includes(label) ? current.filter((item) => item !== label) : [...current, label]
      : [label]);
  };

  const submit = async () => {
    const retryable = active?.status === "answered" && active.assessment !== "correct";
    if (!active || (active.status !== "ready" && !retryable) || submitting) return;
    const answer = buildStructuredAnswer(active.questionPayload.question, selectedOptions, blankAnswers, numericAnswer);
    if (!answer.content.trim()) {
      setError("请先输入或选择答案");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const answered = await answerVariation(active.variationId, answer);
      setItems((current) => current.map((item) => item.variationId === answered.variationId ? answered : item));
      if (answered.tutorStage) onStageChange?.(answered.tutorStage);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "答案提交失败");
    } finally {
      setSubmitting(false);
    }
  };

  return {
    items,
    active,
    selectedOptions,
    blankAnswers,
    numericAnswer,
    loading,
    submitting,
    error,
    setBlankAnswers,
    setNumericAnswer,
    selectOption,
    generate,
    submit,
  };
}
