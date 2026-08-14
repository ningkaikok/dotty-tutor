import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";
import {
  loadPublishedPublication,
  requestHelp,
} from "../../api";
import { LessonPlayer } from "../../lesson/LessonPlayer";
import { speak, stopSpeech } from "../../speech";
import type {
  ExerciseAttemptRecord,
  PublicationDetail,
  TutorReply,
} from "../../types";
import { PaperLearningProgress } from "./PaperLearningProgress";
import { StudentQuestionWorkspace } from "./StudentQuestionWorkspace";
import { usePublishedLearningSession } from "./usePublishedLearningSession";
import "./student.css";

interface StudentQuestionDraft {
  studentInput: string;
  selectedOptions: string[];
  blankAnswers: Record<string, string>;
  numericAnswer: string;
  drawConnections: Array<[string, string]>;
}

/**
 * 学生专用试卷播放器。
 *
 * 内容工作台的预览刻意不写学习遥测；只有本路由拥有真实学习会话、答案上传和离线重试队列。
 */
export function PublishedPaperApp() {
  const navigate = useNavigate();
  const { publicationId = "" } = useParams();
  const [publication, setPublication] = useState<PublicationDetail | null>(null);
  const [questionIndex, setQuestionIndex] = useState(0);
  const [studentInput, setStudentInput] = useState("");
  const [selectedOptions, setSelectedOptions] = useState<string[]>([]);
  const [blankAnswers, setBlankAnswers] = useState<Record<string, string>>({});
  const [numericAnswer, setNumericAnswer] = useState("");
  const [drawConnections, setDrawConnections] = useState<Array<[string, string]>>([]);
  const [hintLevel, setHintLevel] = useState(0);
  const [showExplanation, setShowExplanation] = useState(false);
  const [mistakeNotice, setMistakeNotice] = useState("");
  const [reply, setReply] = useState<TutorReply | null>(null);
  // 未提交的选择也属于学生当前作答上下文。按题目 ID 保存，允许学生先浏览后提交，
  // 再返回时不会因为切题 effect 而丢掉刚才填写的内容。
  const [drafts, setDrafts] = useState<Record<string, StudentQuestionDraft>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const activeQuestionIdRef = useRef("");
  const interactionRequestId = useRef(0);
  const { queueAttempt, syncMessage, mastery, attempts } = usePublishedLearningSession(publication?.publicationId);

  const payload = publication?.lessons[questionIndex]?.questionPayload ?? null;
  const currentQuestionId = payload?.question.id ?? "";

  const latestAttempt = payload
    ? attempts
      .filter((attempt) => attempt.questionId === payload.question.id)
      .sort((left, right) => right.createdAt - left.createdAt)[0]
    : undefined;

  const restoreAttempt = (attempt?: ExerciseAttemptRecord, draft?: StudentQuestionDraft) => {
    const response = attempt?.response ?? {};
    const interaction = response.interactionResult;
    const structured = interaction && typeof interaction === "object"
      ? interaction as Record<string, unknown>
      : {};
    const selected = attempt ? structured.selectedOptions : draft?.selectedOptions;
    const blanks = attempt ? structured.blankAnswers : draft?.blankAnswers;
    const numeric = attempt ? structured.numericAnswer : draft?.numericAnswer;
    const connections = attempt ? structured.connections : draft?.drawConnections;
    const nextStudentInput = attempt
      ? (typeof response.text === "string" ? response.text : "")
      : (draft?.studentInput ?? "");
    const nextSelectedOptions = Array.isArray(selected) ? selected.filter((item): item is string => typeof item === "string") : [];
    const nextBlankAnswers = blanks && typeof blanks === "object" && !Array.isArray(blanks)
      ? Object.fromEntries(Object.entries(blanks).filter(([, value]) => typeof value === "string")) as Record<string, string>
      : {};
    const nextNumericAnswer = typeof numeric === "string" ? numeric : "";
    const nextDrawConnections = Array.isArray(connections)
      ? connections.filter((item): item is [string, string] => Array.isArray(item) && item.length === 2 && item.every((value) => typeof value === "string"))
      : [];
    setStudentInput(nextStudentInput);
    setSelectedOptions(nextSelectedOptions);
    setBlankAnswers(nextBlankAnswers);
    setNumericAnswer(nextNumericAnswer);
    setDrawConnections(nextDrawConnections);
    setHintLevel(attempt?.hintLevel ?? 0);
    if (currentQuestionId) {
      setDrafts((current) => ({
        ...current,
        [currentQuestionId]: {
          studentInput: nextStudentInput,
          selectedOptions: nextSelectedOptions,
          blankAnswers: nextBlankAnswers,
          numericAnswer: nextNumericAnswer,
          drawConnections: nextDrawConnections,
        },
      }));
    }
  };

  const updateDraft = (patch: Partial<StudentQuestionDraft>) => {
    if (!currentQuestionId) return;
    setDrafts((current) => ({
      ...current,
      [currentQuestionId]: {
        studentInput: current[currentQuestionId]?.studentInput ?? "",
        selectedOptions: current[currentQuestionId]?.selectedOptions ?? [],
        blankAnswers: current[currentQuestionId]?.blankAnswers ?? {},
        numericAnswer: current[currentQuestionId]?.numericAnswer ?? "",
        drawConnections: current[currentQuestionId]?.drawConnections ?? [],
        ...patch,
      },
    }));
  };

  useEffect(() => {
    if (!publicationId) return;
    loadPublishedPublication(publicationId)
      .then(setPublication)
      .catch((requestError) => setError(requestError instanceof Error ? requestError.message : "试卷加载失败"));
  }, [publicationId]);

  useEffect(() => {
    activeQuestionIdRef.current = payload?.question.id ?? "";
  }, [payload?.question.id]);

  useEffect(() => {
    interactionRequestId.current += 1;
    stopSpeech();
    setStudentInput("");
    setSelectedOptions([]);
    setBlankAnswers({});
    setNumericAnswer("");
    setDrawConnections([]);
    setHintLevel(0);
    setReply(null);
    setShowExplanation(false);
    setMistakeNotice("");
    setLoading(false);
    restoreAttempt(latestAttempt, latestAttempt ? undefined : drafts[currentQuestionId]);
  // 题目切换时恢复该题最后一次提交的结构化答案；模型回复不从历史猜测，避免把旧题
  // 的讲解误显示到新题上。attempts 变化单独由下面的 effect 处理。
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [questionIndex, payload?.question.id]);

  useEffect(() => {
    // 首次加载会话是异步的，题目可能已经先渲染。只有当前控件为空时才回填，
    // 防止学生正在编辑时后台同步覆盖输入。
    if (!latestAttempt || studentInput || selectedOptions.length || Object.keys(blankAnswers).length
      || numericAnswer || drawConnections.length) return;
    restoreAttempt(latestAttempt);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempts, payload?.question.id]);

  useEffect(() => () => {
    interactionRequestId.current += 1;
    stopSpeech();
  }, []);

  const askTutor = async (mode: "answer" | "help") => {
    if (!payload || loading) return;
    // 作答是静音阶段：如果学生刚看过提示后开始重新选择或提交答案，
    // 立即停止旧语音，避免 TTS 与当前的思考过程并行占用请求。
    if (mode === "answer") stopSpeech();
    const questionId = payload.question.id;
    const requestId = ++interactionRequestId.current;
    const questionType = payload.question.questionType;
    const interactionResult = questionType === "fill-blank"
      ? { blankAnswers }
      : questionType === "numeric"
        ? { numericAnswer }
        : questionType === "multi-select" || questionType === "choice" || questionType === "true-false"
          ? { selectedOptions }
          : questionType === "draw-line" ? { connections: drawConnections } : undefined;
    const submittedInput = studentInput
      || (questionType === "fill-blank" ? Object.values(blankAnswers).join("；") : "")
      || (questionType === "numeric" ? numericAnswer : "")
      || (selectedOptions.length ? `我选择${selectedOptions.join("、")}` : "")
      || (questionType === "draw-line" ? "我完成了画线作答" : "");
    if (mode === "answer" && !submittedInput.trim() && !drawConnections.length) {
      setError("请先完成作答");
      return;
    }
    setLoading(true);
    setError("");
    if (mode === "help") setShowExplanation(true);
    const startedAt = performance.now();
    try {
      const response = await requestHelp({
        questionId,
        publicationId,
        studentInput: submittedInput,
        hintLevel,
        mode,
        interactionResult,
      });
      // 切题后旧的辅导请求可能晚返回；不允许它重新触发旧题目的回复或 TTS。
      if (requestId !== interactionRequestId.current || activeQuestionIdRef.current !== questionId) return;
      setReply(response);
      setHintLevel(response.nextHintLevel);
      // 学生提交答案是判题动作，不应隐式启动昂贵的 TTS 请求。
      // 语音属于“请求讲解”的显式反馈；否则每次选择选项、重试答案都会
      // 生成一段音频，多个题目快速切换时还会堆积 /api/tts 请求。
      if (mode === "help") speak(response.reply.replace(/\n/g, " "));
      if (mode === "answer" && response.guideContext.assessment) {
        const attemptResult = await queueAttempt({
          attemptId: crypto.randomUUID(),
          questionId,
          knowledgePoint: payload.question.knowledgePoint,
          response: { text: submittedInput, interactionResult: interactionResult ?? {} },
          assessment: response.guideContext.assessment,
          hintLevel,
          durationMs: Math.round(performance.now() - startedAt),
          createdAt: Date.now() / 1000,
        });
        if (requestId !== interactionRequestId.current || activeQuestionIdRef.current !== questionId) return;
        if (response.guideContext.assessment !== "correct") {
          setShowExplanation(true);
          setMistakeNotice(attemptResult.status === "saved" && attemptResult.autoMistake
            ? "这道错题已自动加入错题本，不需要再次上传。"
            : "错题记录已在本机排队，网络恢复后会自动加入错题本。");
        }
      }
    } catch (requestError) {
      if (requestId !== interactionRequestId.current) return;
      setError(requestError instanceof Error ? requestError.message : "请求失败");
    } finally {
      if (requestId === interactionRequestId.current) setLoading(false);
    }
  };

  const changeQuestion = (nextIndex: number) => {
    // 先取消网络和播放，再提交索引变更；不用等 React effect 执行，切题动作本身就是取消边界。
    interactionRequestId.current += 1;
    stopSpeech();
    setQuestionIndex(nextIndex);
  };

  const selectOption = (label: string, answerText: string) => {
    // 选项变化表示学生重新进入作答，不应继续播放上一轮提示。
    stopSpeech();
    const multiple = payload?.question.questionType === "multi-select" || payload?.question.selectionMode === "multiple";
    const next = multiple
      ? (selectedOptions.includes(label) ? selectedOptions.filter((item) => item !== label) : [...selectedOptions, label])
      : [label];
    setSelectedOptions(next);
    const nextStudentInput = `我选择${next.join("、")}${answerText && !multiple ? `：${answerText}` : ""}`;
    setStudentInput(nextStudentInput);
    updateDraft({ selectedOptions: next, studentInput: nextStudentInput });
    setReply(null);
    setError("");
  };

  if (error && !publication) return <main className="center-state"><strong>无法打开互动试卷</strong><span>{error}</span><button onClick={() => navigate("/learn")}>返回学生空间</button></main>;
  if (!publication || !payload) return <main className="center-state"><span>正在打开互动试卷…</span></main>;

  return (
    <main className="app-shell">
      <header className="topbar student-paper-topbar">
        <button className="product-home-button" onClick={() => navigate("/learn")}>← 返回学生空间</button>
        <div className="brand-mark">D</div>
        <div><strong>Dotty</strong><span>{publication.title}</span></div>
        <span className="active-model live paper-count-badge">第 {questionIndex + 1}/{publication.lessons.length} 题</span>
        <span className="active-model live paper-sync-badge">{syncMessage}</span>
      </header>
      <PaperLearningProgress
        knowledgePoint={payload.question.knowledgePoint}
        mastery={mastery}
        syncMessage={syncMessage}
      />
      <StudentQuestionWorkspace
        payload={payload}
        questionIndex={questionIndex}
        questionCount={publication.lessons.length}
        loading={loading}
        selectedOptions={selectedOptions}
        blankAnswers={blankAnswers}
        numericAnswer={numericAnswer}
        drawConnections={drawConnections}
        studentInput={studentInput}
        error={error}
        reply={reply}
        mistakeNotice={mistakeNotice}
        hasSubmitted={Boolean(latestAttempt)}
        onPrevious={() => changeQuestion(Math.max(0, questionIndex - 1))}
        onNext={() => changeQuestion(Math.min(publication.lessons.length - 1, questionIndex + 1))}
        onSelectOption={selectOption}
        onBlankChange={(id, value) => {
          stopSpeech();
          const next = { ...blankAnswers, [id]: value };
          setBlankAnswers(next);
          updateDraft({ blankAnswers: next });
        }}
        onNumericChange={(value) => {
          stopSpeech();
          setNumericAnswer(value);
          updateDraft({ numericAnswer: value });
        }}
        onDrawConnectionsChange={(connections) => {
          stopSpeech();
          setDrawConnections(connections);
          updateDraft({ drawConnections: connections });
        }}
        onStudentInputChange={(value) => {
          stopSpeech();
          setStudentInput(value);
          updateDraft({ studentInput: value });
        }}
        onSubmit={() => void askTutor("answer")}
        onHelp={() => void askTutor("help")}
        onOpenMistakes={() => navigate("/mistakes")}
      />
      {showExplanation && (
        <section className="student-explanation-section" aria-label="分步讲解">
          <div className="student-explanation-heading">
            <span className="eyebrow">按需讲解</span>
            <h2>换一种方式理解这道题</h2>
            <p>讲解只在你请求提示或答案需要修正时出现。</p>
          </div>
          <LessonPlayer payload={payload} studentMode />
        </section>
      )}
    </main>
  );
}
