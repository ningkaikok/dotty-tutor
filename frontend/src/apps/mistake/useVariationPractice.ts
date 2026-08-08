import { useEffect, useState } from "react";
import { answerVariation, createVariation, listVariations } from "../../api";
import type { VariationExercise } from "../../types";
import { buildStructuredAnswer } from "./structuredAnswer";

/** Manage one scored attempt without mixing it into free-form tutor messages. */
export function useVariationPractice(mistakeId: string) {
  const [items, setItems] = useState<VariationExercise[]>([]);
  const [selectedOptions, setSelectedOptions] = useState<string[]>([]);
  const [blankAnswers, setBlankAnswers] = useState<Record<string, string>>({});
  const [numericAnswer, setNumericAnswer] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
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

  const resetDraft = () => {
    setSelectedOptions([]);
    setBlankAnswers({});
    setNumericAnswer("");
  };

  const generate = async () => {
    if (submitting || active?.status === "ready") return;
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

  const selectOption = (label: string) => {
    if (!active || active.status === "answered") return;
    const question = active.questionPayload.question;
    const multiple = question.questionType === "multi-select" || question.selectionMode === "multiple";
    setSelectedOptions((current) => multiple
      ? current.includes(label) ? current.filter((item) => item !== label) : [...current, label]
      : [label]);
  };

  const submit = async () => {
    if (!active || active.status !== "ready" || submitting) return;
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
