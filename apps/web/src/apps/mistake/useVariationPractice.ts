import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { answerVariation, createVariation, listVariations } from "../../api/practice";
import type { VariationExercise } from "../../types/index";
import type { TutorStage } from "../../types/index";
import { buildStructuredAnswer } from "./structuredAnswer";

/** 管理一次可计分的变式作答，不把掌握证据混入自由对话消息。 */
const MAX_AUTOMATIC_CONTINUOUS_VARIATIONS = 4;

export function useVariationPractice(
  mistakeId: string,
  autoStart = false,
  onStageChange?: (stage: TutorStage) => void,
  autoContinue = false,
  autoContinuationPaused = false,
) {
  const [items, setItems] = useState<VariationExercise[]>([]);
  const [selectedOptions, setSelectedOptions] = useState<string[]>([]);
  const [blankAnswers, setBlankAnswers] = useState<Record<string, string>>({});
  const [numericAnswer, setNumericAnswer] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const autoStarted = useRef(false);
  const generationInFlight = useRef(false);
  const autoFollowedAttempt = useRef("");
  const autoContinuationPausedRef = useRef(autoContinuationPaused);
  useLayoutEffect(() => { autoContinuationPausedRef.current = autoContinuationPaused; }, [autoContinuationPaused]);
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
  const resetDraft = useCallback(() => {
    setSelectedOptions([]);
    setBlankAnswers({});
    setNumericAnswer("");
  }, []);

  const generate = useCallback(async () => {
    // A correct answer that has not passed the domain gate needs another
    // evidence-bearing exercise; a mastered item remains terminal.
    if (submitting || generationInFlight.current || active?.mastery?.mastered) return;
    // State updates can be batched while two click handlers are already queued.
    // Keep the one-request limit synchronous so a manual continuation never
    // fans out into multiple generated exercises.
    generationInFlight.current = true;
    setSubmitting(true);
    setError("");
    try {
      const created = await createVariation(mistakeId);
      setItems((current) => [...current, created]);
      resetDraft();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "验证题生成失败");
    } finally {
      generationInFlight.current = false;
      setSubmitting(false);
    }
  }, [active?.mastery?.mastered, mistakeId, resetDraft, submitting]);

  useEffect(() => {
    if (!autoStart || loading || items.length > 0 || autoStarted.current || submitting) return;
    autoStarted.current = true;
    void generate();
  }, [autoStart, generate, items.length, loading, submitting]);

  useEffect(() => {
    const attemptId = active?.attemptId;
    if (!autoContinue || autoContinuationPaused || loading || submitting || !active || !attemptId
      || active.status !== "answered" || active.assessment !== "correct"
      || active.mastery?.mastered || active.sequence >= MAX_AUTOMATIC_CONTINUOUS_VARIATIONS
      || autoFollowedAttempt.current === attemptId) return;
    const timeout = window.setTimeout(() => {
      // A learner can pause while the timeout is queued. Record the attempt only
      // after the callback actually starts, so resuming can still prepare it.
      if (autoContinuationPausedRef.current) return;
      autoFollowedAttempt.current = attemptId;
      void generate();
    }, 1200);
    return () => window.clearTimeout(timeout);
  }, [active, autoContinue, autoContinuationPaused, generate, loading, submitting]);

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
      const attemptId = typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
      const answered = await answerVariation(active.variationId, { ...answer, attemptId });
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
    automaticContinuationCapped: Boolean(autoContinue && active?.status === "answered"
      && active.assessment === "correct" && !active.mastery?.mastered
      && active.sequence >= MAX_AUTOMATIC_CONTINUOUS_VARIATIONS),
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
