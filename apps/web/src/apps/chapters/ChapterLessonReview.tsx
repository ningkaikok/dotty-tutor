import { useEffect, useState } from "react";
import { LessonPlayer } from "../../lesson/LessonPlayer";
import type { ChapterLesson, ChapterLessonEditInput } from "../../types/chapter";
import type { LessonDocument } from "../../types/lesson";

interface ChapterLessonReviewProps {
  lessons: ChapterLesson[];
  busyLessonId: string;
  busyAction: "edit" | "review" | "";
  onEdit: (lesson: ChapterLesson, value: ChapterLessonEditInput) => Promise<unknown>;
  onReview: (lesson: ChapterLesson, decision: "approve" | "request_changes") => void;
  onFocusSource: (lesson: ChapterLesson) => void;
}

export function ChapterLessonReview({ lessons, busyLessonId, busyAction, onEdit, onReview, onFocusSource }: ChapterLessonReviewProps) {
  return (
    <section className="chapter-lesson-review" aria-label="课程内容审核">
      <header><div><span className="eyebrow">模板生成 · 教师复核</span><h2>概念、例式、提示与检查题</h2></div></header>
      {lessons.map((lesson) => <ChapterLessonCard
        key={lesson.lessonId}
        lesson={lesson}
        busy={busyLessonId === lesson.lessonId ? busyAction : ""}
        onEdit={onEdit}
        onReview={onReview}
        onFocusSource={onFocusSource}
      />)}
      {!lessons.length && <p>还没有生成课程内容。</p>}
    </section>
  );
}

function ChapterLessonCard({
  lesson,
  busy,
  onEdit,
  onReview,
  onFocusSource,
}: {
  lesson: ChapterLesson;
  busy: "edit" | "review" | "";
  onEdit: ChapterLessonReviewProps["onEdit"];
  onReview: ChapterLessonReviewProps["onReview"];
  onFocusSource: ChapterLessonReviewProps["onFocusSource"];
}) {
  const question = lesson.questionPayload?.question;
  const [editing, setEditing] = useState(false);
  const [prompt, setPrompt] = useState(question?.prompt ?? "");
  const [answer, setAnswer] = useState(question?.answerSpec?.expected ?? question?.correctAnswers?.[0] ?? "");
  const [answerType, setAnswerType] = useState<"text" | "numeric">(question?.questionType === "numeric" ? "numeric" : "text");
  const [questionKind, setQuestionKind] = useState<ChapterLessonEditInput["questionKind"]>(question?.questionKind ?? "short_answer");
  const [answerMode, setAnswerMode] = useState<ChapterLessonEditInput["answerMode"]>(question?.answerMode ?? "short_answer");
  const [acceptedAnswers, setAcceptedAnswers] = useState((question?.acceptedAnswers ?? question?.correctAnswers ?? []).join("\n"));
  const [requiredEvidenceRefs, setRequiredEvidenceRefs] = useState(question?.requiredEvidenceRefs ?? lesson.evidenceOptions ?? []);
  const [rubricText, setRubricText] = useState(JSON.stringify(question?.rubric ?? {}, null, 2));
  const [conceptMarkdown, setConceptMarkdown] = useState(lesson.blocks.find((block) => block.type === "markdown")?.payload.markdown ?? "");
  const [exampleText, setExampleText] = useState(lesson.blocks.find((block) => block.type === "annotation")?.payload.text ?? "");
  const [hint, setHint] = useState(lesson.blocks.find((block) => block.type === "hint")?.payload.hint ?? "");
  const [formError, setFormError] = useState("");

  const previewDocument: LessonDocument = {
    lessonId: lesson.lessonId,
    title: lesson.title,
    version: lesson.version,
    status: "draft",
    sourceUploadId: lesson.sourceUploadId,
    knowledgePoints: lesson.knowledgePoints,
    blocks: lesson.blocks,
  };

  useEffect(() => {
    if (editing) return;
    setPrompt(question?.prompt ?? "");
    setAnswer(question?.answerSpec?.expected ?? question?.correctAnswers?.[0] ?? "");
    setQuestionKind(question?.questionKind ?? "short_answer");
    setAnswerMode(question?.answerMode ?? "short_answer");
    setAcceptedAnswers((question?.acceptedAnswers ?? question?.correctAnswers ?? []).join("\n"));
    setRequiredEvidenceRefs(question?.requiredEvidenceRefs ?? lesson.evidenceOptions ?? []);
    setRubricText(JSON.stringify(question?.rubric ?? {}, null, 2));
    setConceptMarkdown(lesson.blocks.find((block) => block.type === "markdown")?.payload.markdown ?? "");
    setExampleText(lesson.blocks.find((block) => block.type === "annotation")?.payload.text ?? "");
    setHint(lesson.blocks.find((block) => block.type === "hint")?.payload.hint ?? "");
  }, [editing, lesson.blocks, lesson.evidenceOptions, lesson.lessonId, question?.acceptedAnswers, question?.answerMode, question?.prompt, question?.questionKind, question?.requiredEvidenceRefs, question?.rubric, question?.answerSpec?.expected, question?.correctAnswers]);

  return (
    <article className="chapter-lesson-card" aria-label={lesson.title}>
      <div className="chapter-lesson-heading">
        <div><h3>{lesson.title}</h3><span className={`chapter-status ${lesson.status}`}>{lesson.status === "approved" ? "已审核" : lesson.status === "needs_review" ? "需复核" : "待审核"}</span></div>
        <button type="button" onClick={() => onFocusSource(lesson)}>回看第 {lesson.sourceLocator.page} 页来源区域</button>
      </div>
      {lesson.reviewIssues.map((issue) => <p className="chapter-issue blocking" role="status" key={`${issue.code}-${issue.message}`}>{issue.message}</p>)}
      <div className="chapter-lesson-generated-label">模板生成内容 · 审核前不会进入学生端</div>
      <LessonPlayer document={previewDocument} studentMode />
      {question && (
        <section className="chapter-check-question" aria-label={`检查题：${lesson.title}`}>
          <h4>检查题</h4>
          <p>{question.prompt}</p>
          {lesson.status !== "approved" && <p className="chapter-author-answer">审核答案：{question.answerSpec?.expected ?? question.correctAnswers?.join(" / ") ?? "尚未补充；发布会会被阻止"}</p>}
          {!editing ? <button type="button" disabled={Boolean(busy)} onClick={() => setEditing(true)}>{answer ? "编辑检查题" : "补充检查题"}</button> : (
            <form className="chapter-check-editor" onSubmit={async (event) => {
              event.preventDefault();
              setFormError("");
              let rubric: Record<string, unknown>;
              try {
                const value: unknown = JSON.parse(rubricText || "{}");
                if (!value || Array.isArray(value) || typeof value !== "object") throw new Error();
                rubric = value as Record<string, unknown>;
              } catch {
                setFormError("评分依据必须是有效的 JSON 对象");
                return;
              }
              const result = await onEdit(lesson, {
                prompt: prompt.trim(), answer: answer.trim(), answerType, questionKind, answerMode,
                acceptedAnswers: acceptedAnswers.split("\n").map((item) => item.trim()).filter(Boolean),
                requiredEvidenceRefs: requiredEvidenceRefs.map((ref) => ({
                  sourceRevisionId: ref.sourceRevisionId,
                  page: ref.page,
                  ...(ref.regionId ? { regionId: ref.regionId } : {}),
                  ...(ref.sentenceId ? { sentenceId: ref.sentenceId } : {}),
                  ...(ref.quote ? { quote: ref.quote } : {}),
                })),
                rubric, conceptMarkdown, exampleText, hint,
                sourceRevisionId: lesson.sourceRevisionId, page: lesson.sourceLocator.page,
              });
              if (result) setEditing(false);
            }}>
              <label>检查题<input value={prompt} onChange={(event) => setPrompt(event.target.value)} required /></label>
              <label>答案类型<select value={answerType} onChange={(event) => setAnswerType(event.target.value as "text" | "numeric")}><option value="text">文本依据</option><option value="numeric">数字/公式</option></select></label>
              <label>标准答案<input value={answer} onChange={(event) => setAnswer(event.target.value)} required /></label>
              <label>题型<select value={questionKind} onChange={(event) => setQuestionKind(event.target.value as ChapterLessonEditInput["questionKind"])}><option value="word_meaning">词义</option><option value="reference">指代</option><option value="explicit">明示信息</option><option value="inference">有依据推断</option><option value="short_answer">简答</option></select></label>
              <label>判定方式<select value={answerMode} onChange={(event) => setAnswerMode(event.target.value as ChapterLessonEditInput["answerMode"])}><option value="objective">客观判定</option><option value="short_answer">简答复核</option></select></label>
              <label>可接受答案（每行一个）<textarea rows={3} value={acceptedAnswers} onChange={(event) => setAcceptedAnswers(event.target.value)} /></label>
              <fieldset><legend>要求学生选择的原文证据</legend>
                {lesson.evidenceOptions?.length ? lesson.evidenceOptions.map((option) => {
                  const checked = requiredEvidenceRefs.some((ref) => ref.sentenceId === option.sentenceId && ref.regionId === option.regionId && ref.page === option.page);
                  return <label key={`${option.page}-${option.sentenceId ?? option.regionId ?? option.quote}`}><input type="checkbox" checked={checked} onChange={() => setRequiredEvidenceRefs((current) => checked
                    ? current.filter((ref) => !(ref.sentenceId === option.sentenceId && ref.regionId === option.regionId && ref.page === option.page))
                    : [...current, option])} />第 {option.page} 页 · {option.label || option.quote}</label>;
                }) : <p>当前没有可选原文句段；请先补充可引用的句子或区域。</p>}
              </fieldset>
              <label>评分依据 JSON<textarea rows={4} value={rubricText} onChange={(event) => setRubricText(event.target.value)} /></label>
              <label>概念讲解<textarea rows={3} value={conceptMarkdown} onChange={(event) => setConceptMarkdown(event.target.value)} /></label>
              <label>来源例题/例句<textarea rows={3} value={exampleText} onChange={(event) => setExampleText(event.target.value)} /></label>
              <label>提示<textarea rows={2} value={hint} onChange={(event) => setHint(event.target.value)} /></label>
              {formError && <p role="alert">{formError}</p>}
              <div><button type="button" disabled={Boolean(busy)} onClick={() => setEditing(false)}>取消</button><button type="submit" disabled={Boolean(busy)}>{busy === "edit" ? "保存中…" : "保存题目"}</button></div>
            </form>
          )}
        </section>
      )}
      <div className="chapter-review-actions">
        <button type="button" disabled={Boolean(busy)} onClick={() => onReview(lesson, "request_changes")}>{busy === "review" ? "保存中…" : "标记待修改"}</button>
        <button type="button" className="primary" disabled={Boolean(busy) || !question?.prompt || !(question.answerSpec?.expected || question.correctAnswers?.length)} onClick={() => onReview(lesson, "approve")}>{busy === "review" ? "保存中…" : "确认已复核"}</button>
      </div>
    </article>
  );
}
