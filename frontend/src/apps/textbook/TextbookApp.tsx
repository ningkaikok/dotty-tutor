import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { createLearningSession, processPdfBatch, recordExerciseAttempt, requestHelp } from "../../api";
import { PracticeWorkspace } from "../../components/PracticeWorkspace";
import { LessonPlayer } from "../../lesson/LessonPlayer";
import { speak, stopSpeech } from "../../speech";
import { TextbookImport } from "../../TextbookImport";
import type { CanvasAction, QuestionPayload, TextbookImportResult, TutorReply } from "../../types";

const INITIAL_ACTION: CanvasAction = "show-base";
const QUESTION_LIMIT = 5;

export function TextbookApp() {
  // This route component is the textbook product's orchestration boundary. It
  // owns question/session state; child components remain controlled renderers
  // and never call the backend directly.
  const navigate = useNavigate();
  const onExit = () => navigate("/");
  const [payload, setPayload] = useState<QuestionPayload | null>(null);
  const [questionBank, setQuestionBank] = useState<QuestionPayload[]>([]);
  const [questionIndex, setQuestionIndex] = useState(0);
  const [textbookImport, setTextbookImport] = useState<TextbookImportResult | null>(null);
  const [loadError, setLoadError] = useState("");
  const [canvasAction, setCanvasAction] = useState<CanvasAction>(INITIAL_ACTION);
  const [studentInput, setStudentInput] = useState("");
  const [selectedOptions, setSelectedOptions] = useState<string[]>([]);
  const [blankAnswers, setBlankAnswers] = useState<Record<string, string>>({});
  const [numericAnswer, setNumericAnswer] = useState("");
  const [drawConnections, setDrawConnections] = useState<Array<[string, string]>>([]);
  const [hintLevel, setHintLevel] = useState(0);
  const [reply, setReply] = useState<TutorReply | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingQuestion, setLoadingQuestion] = useState(false);
  const [interactionError, setInteractionError] = useState("");
  const [learningSessionId, setLearningSessionId] = useState("");
  const [masteryScore, setMasteryScore] = useState<number | null>(null);

  const resetLearningState = () => {
    // Question-scoped state must move together. Resetting only the visible text
    // would leak an old structured answer or audio step into the next question.
    stopSpeech();
    setCanvasAction(INITIAL_ACTION);
    setStudentInput("");
    setSelectedOptions([]);
    setBlankAnswers({});
    setNumericAnswer("");
    setDrawConnections([]);
    setHintLevel(0);
    setReply(null);
    setLoadError("");
    setInteractionError("");
  };

  const returnToLibrary = () => {
    resetLearningState();
    setPayload(null);
    setTextbookImport(null);
  };

  useEffect(() => {
    if (!payload) {
      setLearningSessionId("");
      return;
    }
    let cancelled = false;
    setMasteryScore(null);
    void createLearningSession({ learnerId: "local-demo", lessonId: payload.question.id })
      .then((session) => {
        if (!cancelled) setLearningSessionId(session.sessionId);
      })
      .catch(() => {
        if (!cancelled) setLearningSessionId("");
      });
    return () => { cancelled = true; };
  }, [payload?.question.id]);

  const askTutor = async (mode: "answer" | "help") => {
    if (!payload || loading) return;
    const questionType = payload.question.questionType;
    const isDrawQuestion = questionType === "draw-line";
    const structuredResult = questionType === "fill-blank"
      ? { blankAnswers }
      : questionType === "numeric"
        ? { numericAnswer }
        : questionType === "multi-select" || questionType === "choice" || questionType === "true-false"
          ? { selectedOptions }
          : isDrawQuestion ? { connections: drawConnections } : undefined;
    const submittedInput = studentInput
      || (questionType === "fill-blank" ? Object.values(blankAnswers).join("；") : "")
      || (questionType === "numeric" ? numericAnswer : "")
      || (selectedOptions.length ? `我选择${selectedOptions.join("、")}` : "")
      || (isDrawQuestion ? "我完成了画线作答" : "");
    if (mode === "answer" && !submittedInput.trim() && (!isDrawQuestion || !drawConnections.length)) {
      setInteractionError("请先写下你的答案或推导步骤");
      return;
    }
    setLoading(true);
    setInteractionError("");
    const startedAt = performance.now();
    try {
      const response = await requestHelp({
        questionId: payload.question.id,
        studentInput: submittedInput,
        hintLevel,
        mode,
        interactionResult: structuredResult,
      });
      setReply(response);
      setHintLevel(response.nextHintLevel);
      setCanvasAction(response.canvasAction);
      speak(response.reply.replace(/\n/g, " "));
      if (learningSessionId && response.guideContext.assessment) {
        // Mastery telemetry is intentionally fire-and-forget: recording failure
        // must not hide an otherwise valid tutor response from the student.
        void recordExerciseAttempt(learningSessionId, {
          questionId: payload.question.id,
          knowledgePoint: payload.question.knowledgePoint,
          response: { text: submittedInput, interactionResult: structuredResult ?? {} },
          assessment: response.guideContext.assessment,
          hintLevel,
          durationMs: Math.round(performance.now() - startedAt),
        }).then((result) => setMasteryScore(result.mastery.score)).catch(() => undefined);
      }
    } catch (error) {
      setInteractionError(error instanceof Error ? error.message : "请求失败");
    } finally {
      setLoading(false);
    }
  };

  const selectOption = (label: string, answerText: string) => {
    const isMultiple = payload?.question.questionType === "multi-select" || payload?.question.selectionMode === "multiple";
    const next = isMultiple
      ? (selectedOptions.includes(label) ? selectedOptions.filter((item) => item !== label) : [...selectedOptions, label])
      : [label];
    setSelectedOptions(next);
    setStudentInput(`我选择${next.join("、")}${answerText && !isMultiple ? `：${answerText}` : ""}`);
    setReply(null);
    setInteractionError("");
  };

  const activateQuestion = (index: number, bank = questionBank) => {
    const next = bank[index];
    if (!next) return;
    resetLearningState();
    setQuestionIndex(index);
    setPayload(next);
  };

  const goToNextQuestion = async () => {
    if (questionIndex < questionBank.length - 1) {
      activateQuestion(questionIndex + 1);
      return;
    }
    if (!textbookImport?.uploadId || loadingQuestion || questionBank.length >= QUESTION_LIMIT) return;
    // Later five-page ranges are generated lazily. The demo stays responsive
    // after the first batch and only spends OCR/model time when the learner asks.
    const nextBatch = textbookImport.batches?.find((batch) => batch.status === "queued");
    if (!nextBatch) return;
    setLoadingQuestion(true);
    setInteractionError("");
    try {
      const generated = await processPdfBatch(textbookImport.uploadId, nextBatch.id);
      const generatedQuestions = generated.questionPayloads?.length
        ? generated.questionPayloads
        : [generated.questionPayload];
      const nextBank = [...questionBank, ...generatedQuestions].slice(0, QUESTION_LIMIT);
      setQuestionBank(nextBank);
      setTextbookImport((current) => current ? {
        ...current,
        extraction: { ...current.extraction, questionCount: nextBank.length },
        batches: current.batches?.map((batch) => batch.id === generated.batch.id ? generated.batch : batch),
      } : current);
      activateQuestion(questionBank.length, nextBank);
    } catch (error) {
      setInteractionError(error instanceof Error ? error.message : "下一题生成失败");
    } finally {
      setLoadingQuestion(false);
    }
  };

  const regenerateCurrentQuestion = async () => {
    if (!textbookImport?.uploadId || loadingQuestion) return;
    const currentBatch = textbookImport.batches?.find(
      (batch) => batch.id === payload?.question.sourceBatchId,
    );
    if (!currentBatch) return;
    setLoadingQuestion(true);
    setInteractionError("");
    try {
      const regenerated = await processPdfBatch(textbookImport.uploadId, currentBatch.id, true);
      const generatedQuestions = regenerated.questionPayloads?.length
        ? regenerated.questionPayloads
        : [regenerated.questionPayload];
      const nextBank = questionBank.filter((item) => item.question.sourceBatchId !== currentBatch.id);
      const insertAt = Math.min(questionIndex, nextBank.length);
      nextBank.splice(insertAt, 0, ...generatedQuestions);
      nextBank.splice(QUESTION_LIMIT);
      setQuestionBank(nextBank);
      setPayload(nextBank[insertAt]);
      setQuestionIndex(insertAt);
      resetLearningState();
    } catch (error) {
      setInteractionError(error instanceof Error ? error.message : "题目重新识别失败");
    } finally {
      setLoadingQuestion(false);
    }
  };

  if (!payload || !textbookImport) {
    return (
      <TextbookImport
        onExit={onExit}
        onContinue={(result) => {
          resetLearningState();
          const importedBank = (result.questionPayloads?.length ? result.questionPayloads : [result.questionPayload])
            .slice(0, QUESTION_LIMIT);
          setTextbookImport({
            ...result,
            extraction: { ...result.extraction, questionCount: importedBank.length, questionLimit: QUESTION_LIMIT },
          });
          setQuestionBank(importedBank);
          setQuestionIndex(0);
          setPayload(importedBank[0]);
        }}
      />
    );
  }

  if (loadError) {
    return (
      <main className="center-state">
        <strong>无法连接 Python 后端</strong>
        <span>{loadError}</span>
        <code>cd backend && ../.venv/bin/uvicorn app:app --reload --port 8010</code>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <button className="product-home-button" onClick={onExit} title="返回入口选择">选择入口</button>
        <button className="back-home" onClick={returnToLibrary} title="返回教材库">← 教材库</button>
        <div className="brand-mark">D</div>
        <div>
          <strong>Dotty</strong>
          <span>内容生产预览 · Dotty Tutor MVP</span>
        </div>
        <button
          className="status"
          onClick={returnToLibrary}
          title="返回教材库并重新上传"
        ><i /> {textbookImport.filename}</button>
        <span className={`active-model ${payload.modelRun.fallback ? "fallback" : "live"}`}>
          {payload.modelRun.provider} · {payload.modelRun.model}
        </span>
        {masteryScore !== null && <span className="mastery-badge">掌握度 {Math.round(masteryScore * 100)}%</span>}
      </header>

      <section className="source-strip">
        <span>扫描页</span><strong>{textbookImport.filename}</strong><b>→</b>
        <span>识别完成</span><strong>{textbookImport.extraction.questionCount} 道题 · {textbookImport.extraction.guideCardCount} 张引导卡</strong>
      </section>

      <LessonPlayer payload={payload} onActionChange={setCanvasAction} />

      <PracticeWorkspace
        payload={payload}
        textbookImport={textbookImport}
        questionIndex={questionIndex}
        questionCount={questionBank.length}
        questionLimit={QUESTION_LIMIT}
        loadingQuestion={loadingQuestion}
        loading={loading}
        selectedOptions={selectedOptions}
        blankAnswers={blankAnswers}
        numericAnswer={numericAnswer}
        drawConnections={drawConnections}
        studentInput={studentInput}
        interactionError={interactionError}
        reply={reply}
        onRegenerate={() => void regenerateCurrentQuestion()}
        onPrevious={() => activateQuestion(questionIndex - 1)}
        onNext={() => void goToNextQuestion()}
        onSelectOption={selectOption}
        onBlankChange={(id, value) => {
          setBlankAnswers((current) => ({ ...current, [id]: value }));
          setStudentInput("");
        }}
        onNumericChange={(value) => {
          setNumericAnswer(value);
          setStudentInput("");
        }}
        onDrawConnectionsChange={setDrawConnections}
        onStudentInputChange={setStudentInput}
        onSubmit={() => void askTutor("answer")}
        onHelp={() => void askTutor("help")}
      />

      <footer>
        <span>扫描教材页</span><b>→</b><span>题目与引导卡存库</span><b>→</b>
        <span>学生输入</span><b>→</b><span>Dotty 分步辅导</span>
      </footer>
    </main>
  );
}
