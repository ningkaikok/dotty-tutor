import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { processPdfBatch, regenerateQuestion, requestHelp } from "../../api";
import { PracticeWorkspace } from "../../components/PracticeWorkspace";
import { LessonPlayer } from "../../lesson/LessonPlayer";
import { speak, stopSpeech } from "../../speech";
import { TextbookImport } from "../../TextbookImport";
import type { CanvasAction, QuestionPayload, TextbookImportResult, TutorReply } from "../../types";
import { usePaperPublication } from "./usePaperPublication";

const INITIAL_ACTION: CanvasAction = "show-base";
const QUESTION_LIMIT = 5;

export function TextbookApp() {
  // 本路由是内容生产工作台的编排边界。这里的答题只用于质量预览；真实学生学习记录只允许
  // PublishedPaperApp 创建，避免编辑者试做污染掌握度。
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
  const activeQuestionIdRef = useRef("");
  const interactionRequestId = useRef(0);
  const {
    publication,
    publicationBusy,
    publicationError,
    publicationNotice,
    restoredQuestionBank,
    submitForReview,
    publish,
    regenerateRevision,
    resetPublication,
  } = usePaperPublication(textbookImport, questionBank);

  useEffect(() => {
    // 恢复的是某个试卷版本自己的课程快照，不是重新读取教材批次；这样刷新后仍能继续审核 v2。
    if (!restoredQuestionBank?.length) return;
    const nextBank = restoredQuestionBank.slice(0, QUESTION_LIMIT);
    setQuestionBank(nextBank);
    setQuestionIndex(0);
    setPayload(nextBank[0]);
    setTextbookImport((current) => current ? {
      ...current,
      extraction: { ...current.extraction, questionCount: nextBank.length },
    } : current);
  }, [restoredQuestionBank]);

  const resetLearningState = () => {
    // 题目级状态必须整体移动。只清空文本会把上一题的结构化答案、提示级别或音频步骤带到下一题。
    interactionRequestId.current += 1;
    stopSpeech();
    setLoading(false);
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
    resetPublication();
  };

  const askTutor = async (mode: "answer" | "help") => {
    if (!payload || loading) return;
    const questionId = payload.question.id;
    const requestId = ++interactionRequestId.current;
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
    try {
      const response = await requestHelp({
        questionId,
        studentInput: submittedInput,
        hintLevel,
        mode,
        interactionResult: structuredResult,
      });
      // 切题后旧的辅导请求可能仍在网络中；它的答案和语音都不能回写到新题目。
      if (requestId !== interactionRequestId.current || activeQuestionIdRef.current !== questionId) return;
      setReply(response);
      setHintLevel(response.nextHintLevel);
      setCanvasAction(response.canvasAction);
      speak(response.reply.replace(/\n/g, " "));
    } catch (error) {
      if (requestId !== interactionRequestId.current) return;
      setInteractionError(error instanceof Error ? error.message : "请求失败");
    } finally {
      if (requestId === interactionRequestId.current) setLoading(false);
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

  useEffect(() => {
    activeQuestionIdRef.current = payload?.question.id ?? "";
  }, [payload?.question.id]);

  const goToNextQuestion = async () => {
    if (questionIndex < questionBank.length - 1) {
      activateQuestion(questionIndex + 1);
      return;
    }
    if (!textbookImport?.uploadId || loadingQuestion || questionBank.length >= QUESTION_LIMIT) return;
    // 后续五页批次按需生成：首批完成后页面即可交互，只有继续翻题时才消耗 OCR/模型时间。
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
    const sourceQuestionKey = payload?.question.sourceQuestionKey;
    if (!textbookImport?.uploadId || !sourceQuestionKey || loadingQuestion) return;
    setLoadingQuestion(true);
    setInteractionError("");
    try {
      const regenerated = await regenerateQuestion(textbookImport.uploadId, sourceQuestionKey);
      const nextBank = questionBank.map((item) => (
        item.question.sourceQuestionKey === sourceQuestionKey ? regenerated.questionPayload : item
      ));
      setQuestionBank(nextBank);
      setPayload(regenerated.questionPayload);
      resetLearningState();
    } catch (error) {
      setInteractionError(error instanceof Error ? error.message : "题目修复失败");
    } finally {
      setLoadingQuestion(false);
    }
  };

  const regenerateCurrentBatch = async (refreshOcr = false) => {
    const batchId = payload?.question.sourceBatchId;
    if (!textbookImport?.uploadId || !batchId || loadingQuestion) return;
    setLoadingQuestion(true);
    setInteractionError("");
    try {
      const regenerated = await processPdfBatch(textbookImport.uploadId, batchId, true, refreshOcr);
      const generatedQuestions = regenerated.questionPayloads?.length
        ? regenerated.questionPayloads
        : [regenerated.questionPayload];
      const firstIndex = questionBank.findIndex((item) => item.question.sourceBatchId === batchId);
      const retained = questionBank.filter((item) => item.question.sourceBatchId !== batchId);
      const insertAt = firstIndex < 0 ? retained.length : firstIndex;
      retained.splice(insertAt, 0, ...generatedQuestions);
      const nextBank = retained.slice(0, QUESTION_LIMIT);
      setQuestionBank(nextBank);
      setTextbookImport((current) => current ? {
        ...current,
        extraction: { ...current.extraction, questionCount: nextBank.length },
        batches: current.batches?.map((batch) => batch.id === regenerated.batch.id ? regenerated.batch : batch),
      } : current);
      activateQuestion(Math.min(insertAt, nextBank.length - 1), nextBank);
    } catch (error) {
      setInteractionError(error instanceof Error ? error.message : "批次重新生成失败");
    } finally {
      setLoadingQuestion(false);
    }
  };

  const regenerateWholePaper = async () => {
    if (loadingQuestion || publicationBusy) return;
    setLoadingQuestion(true);
    setInteractionError("");
    try {
      const revisedBank = await regenerateRevision();
      if (!revisedBank?.length) return;
      const nextBank = revisedBank.slice(0, QUESTION_LIMIT);
      setQuestionBank(nextBank);
      activateQuestion(0, nextBank);
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
        <button className="back-home" onClick={returnToLibrary} title="返回教材库">← 返回教材库</button>
        <div className="brand-mark">D</div>
        <div>
          <strong>Dotty</strong>
          <span>内容生产预览 · Dotty Tutor MVP</span>
        </div>
        <span className="status" title="当前教材"><i /> {textbookImport.filename}</span>
        <span className={`active-model ${payload.modelRun.fallback ? "fallback" : "live"}`}>
          {payload.modelRun.provider} · {payload.modelRun.model}
        </span>
        {!publication && (
          <button className="ghost compact" disabled={publicationBusy} onClick={() => void submitForReview()}>
            {publicationBusy ? "提交中…" : "提交试卷审核"}
          </button>
        )}
        {publication?.status === "in_review" && (
          <>
            <button className="ghost compact" disabled={publicationBusy || loadingQuestion} onClick={() => void regenerateWholePaper()}>
              {publicationBusy || loadingQuestion ? "生成新版中…" : "整套重新审核"}
            </button>
            <button className="lesson-button" disabled={publicationBusy} onClick={() => void publish()}>
              {publicationBusy ? "发布中…" : `发布试卷 v${publication.version || 1}`}
            </button>
          </>
        )}
        {publication?.status === "published" && (
          <>
            <button className="ghost compact" disabled={publicationBusy || loadingQuestion} onClick={() => void regenerateWholePaper()}>
              {publicationBusy || loadingQuestion ? "生成新版中…" : "生成审核新版"}
            </button>
            <span className="active-model live">已发布 v{publication.version || 1}</span>
          </>
        )}
      </header>

      {publicationError && <p className="import-error" role="alert">{publicationError}</p>}
      {publicationNotice && <p className="publication-notice" role="status">{publicationNotice}</p>}

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
        onRegenerateBatch={() => void regenerateCurrentBatch()}
        onRegenerateBatchWithOcr={() => void regenerateCurrentBatch(true)}
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
