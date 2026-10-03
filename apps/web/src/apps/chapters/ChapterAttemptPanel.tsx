import type { ChapterAttemptResult, ChapterEvidenceRef, ChapterLesson, ChapterSubject } from "../../types/chapter";

interface ChapterAttemptPanelProps {
  lesson: ChapterLesson;
  subject: ChapterSubject;
  answer: string;
  evidenceRefs: ChapterEvidenceRef[];
  result: ChapterAttemptResult | null;
  busy: boolean;
  error: string;
  onAnswerChange: (value: string) => void;
  onEvidenceChange: (value: ChapterEvidenceRef[]) => void;
  onSubmit: () => void;
}

export function ChapterAttemptPanel({
  lesson,
  subject,
  answer,
  evidenceRefs,
  result,
  busy,
  error,
  onAnswerChange,
  onEvidenceChange,
  onSubmit,
}: ChapterAttemptPanelProps) {
  const options = lesson.evidenceOptions ?? [];
  return (
    <section className="chapter-student-attempt" aria-label="章节检查题作答">
      <span className="eyebrow">{subject === "english" ? "阅读理解 · 原文作证" : "学习检查"}</span>
      <h2>{lesson.questionPayload?.question.prompt || "请回看课程步骤后作答"}</h2>
      <label htmlFor="chapter-answer">你的答案</label>
      <textarea id="chapter-answer" value={answer} onChange={(event) => onAnswerChange(event.target.value)} rows={3} placeholder="写下答案" />
      <fieldset className="chapter-evidence-options">
        <legend>选择支持答案的原文证据</legend>
        {options.map((option) => {
          const checked = evidenceRefs.some((ref) => ref.sourceRevisionId === option.sourceRevisionId
            && ref.page === option.page && ref.sentenceId === option.sentenceId && ref.regionId === option.regionId);
          const { label, ...reference } = option;
          return <label key={`${option.sourceRevisionId}-${option.page}-${option.sentenceId ?? option.regionId ?? option.quote}`}>
            <input type="checkbox" checked={checked} onChange={() => onEvidenceChange(checked
              ? evidenceRefs.filter((ref) => !(ref.sourceRevisionId === option.sourceRevisionId && ref.page === option.page && ref.sentenceId === option.sentenceId && ref.regionId === option.regionId))
              : [...evidenceRefs, reference])} />
            <span>第 {option.page} 页 · {label || option.quote || "来源区域"}</span>
          </label>;
        })}
        {!options.length && <p>课程没有提供可选择的原文句段，请返回教师工作台补充来源证据。</p>}
      </fieldset>
      {error && <p className="chapter-error" role="alert">{error}</p>}
      <button type="button" disabled={busy || !answer.trim() || !evidenceRefs.length} onClick={onSubmit}>{busy ? "正在提交…" : result ? "重新提交" : "提交答案与依据"}</button>
      {result && <section className={`chapter-attempt-feedback ${result.assessment}`} aria-live="polite">
        <strong>{result.assessment === "correct" ? "答案与依据均匹配" : result.assessment === "incorrect" ? "答案或依据需要修正" : subject === "english" ? "待教师复核" : "已保存，请核对题目与依据后重试"}</strong>
        <p>{result.feedback.message}</p>
        {result.assessment === "needs_review" && subject === "english" && <small>学生答案与来源依据已保存；教师确认前不会把这次作答标为正确。</small>}
        <small>依据状态：{result.evidenceVerdict === "supported" ? "支持答案" : result.evidenceVerdict === "mismatch" ? "与题目要求不匹配" : result.evidenceVerdict === "missing" ? "没有选择依据" : "待复核"}</small>
        {result.assessment === "needs_review" && subject === "english" && <small>教师查询编号：<code>{result.attemptId}</code></small>}
      </section>}
    </section>
  );
}
