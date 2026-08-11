import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router";
import {
  loadPublishedPublication,
  requestHelp,
} from "../../api";
import { LessonPlayer } from "../../lesson/LessonPlayer";
import { speak, stopSpeech } from "../../speech";
import type {
  PublicationDetail,
  TutorReply,
} from "../../types";
import { PaperLearningProgress } from "./PaperLearningProgress";
import { StudentQuestionWorkspace } from "./StudentQuestionWorkspace";
import { usePublishedLearningSession } from "./usePublishedLearningSession";
import "./student.css";

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
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const { queueAttempt, syncMessage, mastery } = usePublishedLearningSession(publication?.publicationId);

  const payload = publication?.lessons[questionIndex]?.questionPayload ?? null;

  useEffect(() => {
    if (!publicationId) return;
    loadPublishedPublication(publicationId)
      .then(setPublication)
      .catch((requestError) => setError(requestError instanceof Error ? requestError.message : "试卷加载失败"));
  }, [publicationId]);

  useEffect(() => {
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
  }, [questionIndex]);

  const askTutor = async (mode: "answer" | "help") => {
    if (!payload || loading) return;
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
        questionId: payload.question.id,
        publicationId,
        studentInput: submittedInput,
        hintLevel,
        mode,
        interactionResult,
      });
      setReply(response);
      setHintLevel(response.nextHintLevel);
      speak(response.reply.replace(/\n/g, " "));
      if (mode === "answer" && response.guideContext.assessment) {
        const attemptResult = await queueAttempt({
          attemptId: crypto.randomUUID(),
          questionId: payload.question.id,
          knowledgePoint: payload.question.knowledgePoint,
          response: { text: submittedInput, interactionResult: interactionResult ?? {} },
          assessment: response.guideContext.assessment,
          hintLevel,
          durationMs: Math.round(performance.now() - startedAt),
          createdAt: Date.now() / 1000,
        });
        if (response.guideContext.assessment !== "correct") {
          setShowExplanation(true);
          setMistakeNotice(attemptResult.status === "saved" && attemptResult.autoMistake
            ? "这道错题已自动加入错题本，不需要再次上传。"
            : "错题记录已在本机排队，网络恢复后会自动加入错题本。");
        }
      }
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "请求失败");
    } finally {
      setLoading(false);
    }
  };

  const selectOption = (label: string, answerText: string) => {
    const multiple = payload?.question.questionType === "multi-select" || payload?.question.selectionMode === "multiple";
    const next = multiple
      ? (selectedOptions.includes(label) ? selectedOptions.filter((item) => item !== label) : [...selectedOptions, label])
      : [label];
    setSelectedOptions(next);
    setStudentInput(`我选择${next.join("、")}${answerText && !multiple ? `：${answerText}` : ""}`);
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
        onPrevious={() => setQuestionIndex((value) => Math.max(0, value - 1))}
        onNext={() => setQuestionIndex((value) => Math.min(publication.lessons.length - 1, value + 1))}
        onSelectOption={selectOption}
        onBlankChange={(id, value) => setBlankAnswers((current) => ({ ...current, [id]: value }))}
        onNumericChange={setNumericAnswer}
        onDrawConnectionsChange={setDrawConnections}
        onStudentInputChange={setStudentInput}
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
