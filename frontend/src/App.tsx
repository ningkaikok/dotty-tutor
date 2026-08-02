import { useEffect, useMemo, useState } from "react";
import { processPdfBatch, requestHelp } from "./api";
import { GeometryCanvas } from "./GeometryCanvas";
import { QuestionAnswer } from "./components/QuestionAnswer";
import { TextbookImport } from "./TextbookImport";
import type { CanvasAction, QuestionPayload, TextbookImportResult, TutorReply } from "./types";
import "./styles.css";

const INITIAL_ACTION: CanvasAction = "show-base";
const QUESTION_LIMIT = 5;

let activeAudio: HTMLAudioElement | null = null;
let speechRequestId = 0;

function stopSpeech() {
  speechRequestId += 1;
  window.speechSynthesis?.cancel();
  activeAudio?.pause();
  activeAudio = null;
}

async function speak(text: string) {
  const requestId = ++speechRequestId;
  window.speechSynthesis?.cancel();
  activeAudio?.pause();
  activeAudio = null;
  try {
    const response = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!response.ok) throw new Error("Qwen3-TTS unavailable");
    if (requestId !== speechRequestId) return;
    const audio = new Audio(URL.createObjectURL(await response.blob()));
    activeAudio = audio;
    audio.onended = () => {
      URL.revokeObjectURL(audio.src);
      if (activeAudio === audio) activeAudio = null;
    };
    await audio.play();
  } catch {
    if (requestId !== speechRequestId || !("speechSynthesis" in window)) return;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "zh-CN";
    utterance.rate = 0.95;
    window.speechSynthesis.speak(utterance);
  }
}

export default function App() {
  const [payload, setPayload] = useState<QuestionPayload | null>(null);
  const [questionBank, setQuestionBank] = useState<QuestionPayload[]>([]);
  const [questionIndex, setQuestionIndex] = useState(0);
  const [textbookImport, setTextbookImport] = useState<TextbookImportResult | null>(null);
  const [loadError, setLoadError] = useState("");
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
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
  const [debugOpen, setDebugOpen] = useState(false);

  const currentStep = useMemo(() => payload?.lessonSteps[step], [payload, step]);

  const resetLearningState = () => {
    stopSpeech();
    setStep(0);
    setPlaying(false);
    setCanvasAction(INITIAL_ACTION);
    setStudentInput("");
    setSelectedOptions([]);
    setBlankAnswers({});
    setNumericAnswer("");
    setDrawConnections([]);
    setHintLevel(0);
    setReply(null);
    setDebugOpen(false);
    setLoadError("");
    setInteractionError("");
  };

  useEffect(() => {
    if (!playing || !payload) return;
    const lessonStep = payload.lessonSteps[step];
    setCanvasAction(lessonStep.action);
    speak(lessonStep.speechText);
    const timer = window.setTimeout(() => {
      if (step >= payload.lessonSteps.length - 1) {
        setPlaying(false);
      } else {
        setStep((value) => value + 1);
      }
    }, 4300);
    return () => window.clearTimeout(timer);
  }, [playing, payload, step]);

  const toggleLesson = () => {
    if (!payload) return;
    if (!playing && step >= payload.lessonSteps.length - 1) setStep(0);
    setPlaying((value) => !value);
  };

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

  if (!currentStep) return <main className="center-state">正在加载数字教材…</main>;

  const isDrawLine = payload.question.questionType === "draw-line";
  const hasStructuredAnswer = selectedOptions.length > 0
    || Object.values(blankAnswers).some((value) => value.trim())
    || Boolean(numericAnswer.trim())
    || drawConnections.length > 0;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-mark">D</div>
        <div>
          <strong>Dotty</strong>
          <span>动态教材辅导 · Dotty Tutor MVP</span>
        </div>
        <button
          className="status"
          onClick={() => {
            resetLearningState();
            setPayload(null);
            setTextbookImport(null);
          }}
          title="重新上传教材"
        ><i /> {textbookImport.filename}</button>
        <span className={`active-model ${payload.modelRun.fallback ? "fallback" : "live"}`}>
          {payload.modelRun.provider} · {payload.modelRun.model}
        </span>
      </header>

      <section className="source-strip">
        <span>扫描页</span><strong>{textbookImport.filename}</strong><b>→</b>
        <span>识别完成</span><strong>{textbookImport.extraction.questionCount} 道题 · {textbookImport.extraction.guideCardCount} 张引导卡</strong>
      </section>

      <section className="hero-grid">
        <article className="canvas-card panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{payload.question.chapter}</span>
              <h1>{payload.question.knowledgePoint}</h1>
            </div>
            <button className="lesson-button" onClick={toggleLesson}>
              {playing ? "暂停讲解" : "播放讲解"}
            </button>
          </div>
          <GeometryCanvas
            action={canvasAction}
            topic={payload.question.knowledgePoint}
            title={currentStep.title}
            text={currentStep.text}
          />
          <div className="lesson-progress">
            {payload.lessonSteps.map((item, index) => (
              <button
                key={item.id}
                className={index === step ? "active" : ""}
                onClick={() => {
                  setStep(index);
                  setCanvasAction(item.action);
                  setPlaying(false);
                }}
                aria-label={`切换到步骤 ${index + 1}`}
              />
            ))}
          </div>
        </article>

        <aside className="explanation-card panel">
          <span className="eyebrow">STEP {step + 1} / {payload.lessonSteps.length}</span>
          <h2>{currentStep.title}</h2>
          <p>{currentStep.text}</p>
          <div className="speech-copy">
            <span>speechText</span>
            {currentStep.speechText}
          </div>
          <div className="flow-row">
            <span>Canvas</span><b>+</b><span>Text</span><b>+</b><span>TTS</span>
          </div>
        </aside>
      </section>

      <section className="workspace panel">
        <div className="question-block">
          <div className="question-toolbar">
            <span className="eyebrow">课后练习 · 题目 {questionIndex + 1}/{questionBank.length}</span>
            <div className="question-navigation">
              {textbookImport.uploadId && (
                <button
                  className="ghost compact"
                  disabled={loadingQuestion}
                  onClick={() => void regenerateCurrentQuestion()}
                >{loadingQuestion ? "生成中…" : "重新生成本题"}</button>
              )}
              <button
                className="ghost compact"
                disabled={questionIndex === 0 || loadingQuestion}
                onClick={() => activateQuestion(questionIndex - 1)}
              >上一题</button>
              <button
                className="ghost compact"
                disabled={loadingQuestion || (questionIndex === questionBank.length - 1 && (questionBank.length >= QUESTION_LIMIT || !textbookImport.batches?.some((batch) => batch.status === "queued")))}
                onClick={goToNextQuestion}
              >{questionIndex < questionBank.length - 1 ? "下一题" : questionBank.length >= QUESTION_LIMIT ? "已达 5 题上限" : loadingQuestion ? "正在识别下个 5 页…" : "生成下一题"}</button>
            </div>
          </div>
          <div className="question-source-meta">
            {payload.question.questionNumber && <b>原题 {payload.question.questionNumber}</b>}
            {payload.question.sourcePages && (
              <span>来源第 {payload.question.sourcePages.start}-{payload.question.sourcePages.end} 页</span>
            )}
            {payload.review && (
              <b className={payload.review.needsHumanReview ? "review-warning" : "review-passed"}>
                {payload.review.needsHumanReview ? "需要人工复核" : "双模型审校通过"}
              </b>
            )}
            {payload.quality && (
              <b className={payload.quality.status === "ready" ? "review-passed" : "review-warning"}>
                {payload.quality.status === "ready" ? "结构校验通过" : "结构校验未通过"}
              </b>
            )}
          </div>
          {(payload.question.sourceArtifactUrl || payload.question.promptArtifactUrl) && (
            <div className="artifact-links">
              {payload.question.sourceArtifactUrl && (
                <a href={payload.question.sourceArtifactUrl} target="_blank" rel="noreferrer">查看 OCR Markdown</a>
              )}
              {payload.question.promptArtifactUrl && (
                <a href={payload.question.promptArtifactUrl} target="_blank" rel="noreferrer">查看模型提示词</a>
              )}
            </div>
          )}
          <QuestionAnswer
            question={payload.question}
            selectedOptions={selectedOptions}
            blankAnswers={blankAnswers}
            numericAnswer={numericAnswer}
            drawConnections={drawConnections}
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
          />
          {payload.quality && (payload.quality.errors.length > 0 || payload.quality.warnings.length > 0) && (
            <details className="quality-details">
              <summary>结构质量门禁 · {payload.quality.errors.length} 个错误 · {payload.quality.warnings.length} 个警告</summary>
              {payload.quality.errors.map((error, index) => <p key={`quality-error-${index}`}>⛔ {error}</p>)}
              {payload.quality.warnings.map((warning, index) => <p key={`quality-warning-${index}`}>⚠ {warning}</p>)}
            </details>
          )}
          {payload.review && (
            <details className="review-details">
              <summary>
                审校记录 · 文字 {payload.review.text.confidence}% · 视觉 {payload.review.vision.confidence}% · {payload.review.text.corrections.length} 处修正
              </summary>
              {payload.review.text.corrections.map((item, index) => (
                <p key={`${item.field}-${index}`}><b>{item.field}</b>：{item.original} → {item.corrected}（{item.reason}）</p>
              ))}
              {payload.review.vision.correctAnswer && (
                <p><b>视觉判定答案：</b>{payload.review.vision.correctAnswer}</p>
              )}
              {[...payload.review.text.issues, ...payload.review.vision.issues].map((issue, index) => <p key={`${issue}-${index}`}>⚠ {issue}</p>)}
              {payload.question.visualContext?.map((context, index) => (
                <p key={`visual-${index}`}><b>题图理解：</b>{context.description}</p>
              ))}
            </details>
          )}
          <div className="givens">
            {payload.question.givens.map((given) => <span key={given}>{given}</span>)}
          </div>
        </div>

        <div className="answer-block">
          <label htmlFor="answer">{isDrawLine ? "完成作图后提交" : "写下你的想法"}</label>
          <textarea
            id="answer"
            value={studentInput}
            onChange={(event) => setStudentInput(event.target.value)}
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key === "Enter") void askTutor("answer");
            }}
            placeholder="写下你已经完成的步骤，或者说明自己卡在哪里……"
          />
          <div className="answer-actions">
            <button className="ghost" onClick={() => setStudentInput("我不知道怎么开始")}>我卡住了</button>
            <button className="ghost submit-answer" disabled={loading || (!studentInput.trim() && !hasStructuredAnswer)} onClick={() => void askTutor("answer")}>
              {loading ? "正在分析…" : isDrawLine ? "提交作图" : "提交回答"}
            </button>
            <button className="help-button" disabled={loading} onClick={() => void askTutor("help")}>
              {loading ? "正在分析…" : "Help · 下一步提示"}
            </button>
          </div>
          {interactionError && <p className="interaction-error">{interactionError}</p>}
        </div>
      </section>

      {reply && (
        <section className="tutor-panel panel">
          <div className="avatar">D</div>
          <div className="tutor-content">
            <div className="tutor-meta">
              <strong>Dotty</strong>
              {reply.guideContext.assessment && (
                <b className={`assessment ${reply.guideContext.assessment}`}>
                  {reply.guideContext.assessment === "correct" ? "回答正确" : reply.guideContext.assessment === "incorrect" ? "需要修正" : "部分正确"}
                </b>
              )}
              <span>
                {reply.source === "model-generated"
                  ? `实时生成 · ${reply.modelRun.provider}/${reply.modelRun.model}`
                  : reply.modelRun.fallback
                    ? `模型失败，已回退 · ${reply.modelRun.error ?? "Mock"}`
                    : reply.source === "stored-guide-card" ? "来自预制引导卡" : "已检查学生答案"}
              </span>
            </div>
            {reply.reply.split("\n").map((line, index) => <p key={index}>{line || <br />}</p>)}
            <button className="debug-toggle" onClick={() => setDebugOpen((value) => !value)}>
              {debugOpen ? "收起数据流" : "查看本次 guide_context"}
            </button>
            {debugOpen && (
              <pre>{JSON.stringify(reply.guideContext, null, 2)}</pre>
            )}
          </div>
        </section>
      )}

      <footer>
        <span>扫描教材页</span><b>→</b><span>题目与引导卡存库</span><b>→</b>
        <span>学生输入</span><b>→</b><span>Dotty 分步辅导</span>
      </footer>
    </main>
  );
}
