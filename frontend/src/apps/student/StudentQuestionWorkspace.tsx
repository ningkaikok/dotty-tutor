import { QuestionAnswer } from "../../components/QuestionAnswer";
import MathText from "../../MathText";
import type { QuestionPayload, TutorReply } from "../../types";

interface StudentQuestionWorkspaceProps {
  payload: QuestionPayload;
  questionIndex: number;
  questionCount: number;
  loading: boolean;
  selectedOptions: string[];
  blankAnswers: Record<string, string>;
  numericAnswer: string;
  drawConnections: Array<[string, string]>;
  studentInput: string;
  error: string;
  reply: TutorReply | null;
  mistakeNotice: string;
  hasSubmitted: boolean;
  onPrevious: () => void;
  onNext: () => void;
  onSelectOption: (label: string, answerText: string) => void;
  onBlankChange: (id: string, value: string) => void;
  onNumericChange: (value: string) => void;
  onDrawConnectionsChange: (connections: Array<[string, string]>) => void;
  onStudentInputChange: (value: string) => void;
  onSubmit: () => void;
  onHelp: () => void;
  onOpenMistakes: () => void;
}

/**
 * 学生端只保留“读题、作答、求助、查看反馈”四件事。
 *
 * 内容生产工作台的 OCR、审核、模型和重新生成操作属于教师/作者职责，不应因为复用
 * `PracticeWorkspace` 而泄露到学生任务流；这里仅复用无副作用的结构化作答控件。
 */
export function StudentQuestionWorkspace({
  payload,
  questionIndex,
  questionCount,
  loading,
  selectedOptions,
  blankAnswers,
  numericAnswer,
  drawConnections,
  studentInput,
  error,
  reply,
  mistakeNotice,
  hasSubmitted,
  onPrevious,
  onNext,
  onSelectOption,
  onBlankChange,
  onNumericChange,
  onDrawConnectionsChange,
  onStudentInputChange,
  onSubmit,
  onHelp,
  onOpenMistakes,
}: StudentQuestionWorkspaceProps) {
  const question = payload.question;
  const isDrawLine = question.questionType === "draw-line";
  const hasStructuredAnswer = selectedOptions.length > 0
    || Object.values(blankAnswers).some((value) => value.trim())
    || Boolean(numericAnswer.trim())
    || drawConnections.length > 0;
  const assessment = reply?.guideContext.assessment;

  return (
    <section className="student-paper-panel panel" aria-label={`第 ${questionIndex + 1} 题`}>
      <header className="student-question-header">
        <div>
          <span className="eyebrow">互动试卷 · 第 {questionIndex + 1}/{questionCount} 题</span>
          <h1>{question.knowledgePoint}</h1>
        </div>
        <nav className="student-question-nav" aria-label="题目切换">
          <button disabled={questionIndex === 0 || loading} onClick={onPrevious}>上一题</button>
          <button disabled={questionIndex === questionCount - 1 || loading} onClick={onNext}>下一题</button>
        </nav>
      </header>

      <div className="student-question-body">
        <QuestionAnswer
          question={question}
          selectedOptions={selectedOptions}
          blankAnswers={blankAnswers}
          numericAnswer={numericAnswer}
          drawConnections={drawConnections}
          onSelectOption={onSelectOption}
          onBlankChange={onBlankChange}
          onNumericChange={onNumericChange}
          onDrawConnectionsChange={onDrawConnectionsChange}
        />
        {question.givens.length > 0 && (
          <div className="student-question-givens" aria-label="题目条件（辅助读题）">
            <span className="student-question-givens-heading">题目条件</span>
            {question.givens.map((given) => (
              <span key={given}><MathText text={given} /></span>
            ))}
          </div>
        )}
      </div>

      <div className="student-answer-card">
        <label htmlFor="student-answer">{isDrawLine ? "完成作图后提交" : "补充你的思路（可选）"}</label>
        {!isDrawLine && (
          <textarea
            id="student-answer"
            value={studentInput}
            onChange={(event) => onStudentInputChange(event.target.value)}
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key === "Enter") onSubmit();
            }}
            placeholder="可以写计算过程，也可以只选择上面的答案"
          />
        )}
        <div className="student-answer-actions">
          <button className="student-help-button" disabled={loading} onClick={onHelp}>
            {loading ? "正在分析…" : "我需要提示"}
          </button>
          <button
            className="student-submit-button"
            disabled={loading || (!studentInput.trim() && !hasStructuredAnswer)}
            onClick={onSubmit}
          >
            {loading ? "正在批改…" : isDrawLine
              ? (hasSubmitted ? "重新提交作图" : "提交作图")
              : (hasSubmitted ? "重新提交答案" : "提交答案")}
          </button>
        </div>
        {error && <p className="interaction-error" role="alert">{error}</p>}
      </div>

      {reply && (
        <section className={`student-feedback ${assessment ?? "hint"}`} aria-live="polite">
          <div className="student-feedback-heading">
            <strong>{assessment === "correct" ? "回答正确" : assessment === "incorrect" ? "这一步需要修正" : assessment === "partial" ? "已经接近了" : "给你一个提示"}</strong>
            <span>Dotty</span>
          </div>
          {reply.reply.split("\n").map((line, index) => <p key={index}>{line ? <MathText text={line} /> : <br />}</p>)}
        </section>
      )}

      {mistakeNotice && (
        <aside className="student-mistake-notice" aria-live="polite">
          <span>{mistakeNotice}</span>
          <button onClick={onOpenMistakes}>去错题本订正</button>
        </aside>
      )}
    </section>
  );
}
