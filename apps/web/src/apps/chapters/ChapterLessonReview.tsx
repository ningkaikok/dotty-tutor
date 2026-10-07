import { useEffect, useState } from "react";
import { LessonPlayer } from "../../lesson/LessonPlayer";
import type { ChapterLesson, ChapterLessonEditInput } from "../../types/chapter";
import type { LessonDocument } from "../../types/lesson";

interface ChapterLessonReviewProps {
  lessons: ChapterLesson[];
  busyLessonId: string;
  busyAction: "edit" | "review" | "locked" | "";
  onEdit: (lesson: ChapterLesson, value: ChapterLessonEditInput) => Promise<unknown>;
  onReview: (lesson: ChapterLesson, decision: "approve" | "request_changes") => void;
  onFocusSource: (lesson: ChapterLesson) => void;
}

/** Show teacher-authored criteria while preserving all other rubric fields on save. */
function criteriaText(rubric?: Record<string, unknown>): string {
  return Array.isArray(rubric?.criteria) ? rubric.criteria.filter((item): item is string => typeof item === "string").join("\n") : "";
}

export function ChapterLessonReview({ lessons, busyLessonId, busyAction, onEdit, onReview, onFocusSource }: ChapterLessonReviewProps) {
  return (
    <section className="chapter-lesson-review" aria-label="课程内容审核">
      <header><div><span className="eyebrow">课程草稿 · 教师复核</span><h2>{lessons.length > 0 && lessons.every((lesson) => !lesson.questionPayload?.question) ? "学习目标、讲解、示例与总结" : "概念、例式、提示与检查题"}</h2></div></header>
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
  busy: "edit" | "review" | "locked" | "";
  onEdit: ChapterLessonReviewProps["onEdit"];
  onReview: ChapterLessonReviewProps["onReview"];
  onFocusSource: ChapterLessonReviewProps["onFocusSource"];
}) {
  const question = lesson.questionPayload?.question;
  const primaryAnswer = question?.answerSpec?.expected ?? question?.correctAnswers?.[0] ?? question?.acceptedAnswers?.[0] ?? "";
  const suggestedVariants = question?.teacherVariants ?? [];
  const variantsConfirmed = question?.variantReviewStatus === "teacher_confirmed" || question?.variantReviewStatus === "teacher_approved" || question?.variantReviewStatus === "approved";
  const [editing, setEditing] = useState(false);
  const [prompt, setPrompt] = useState(question?.prompt ?? "");
  const [answer, setAnswer] = useState(primaryAnswer);
  const [answerType, setAnswerType] = useState<"text" | "numeric">(question?.questionType === "numeric" ? "numeric" : "text");
  const [questionKind, setQuestionKind] = useState<ChapterLessonEditInput["questionKind"]>(question?.questionKind ?? "short_answer");
  const [answerMode, setAnswerMode] = useState<ChapterLessonEditInput["answerMode"]>(question?.answerMode ?? "short_answer");
  const [acceptedAnswers, setAcceptedAnswers] = useState((question?.acceptedAnswers ?? question?.correctAnswers ?? []).join("\n"));
  const [variantApprovals, setVariantApprovals] = useState<string[]>([]);
  const [requiredEvidenceRefs, setRequiredEvidenceRefs] = useState(question?.requiredEvidenceRefs ?? lesson.evidenceOptions ?? []);
  const [rubricText, setRubricText] = useState(criteriaText(question?.rubric));
  const [conceptMarkdown, setConceptMarkdown] = useState(lesson.blocks.find((block) => block.type === "markdown")?.payload.markdown ?? "");
  const [exampleText, setExampleText] = useState(lesson.blocks.find((block) => block.type === "annotation")?.payload.text ?? "");
  const [hints, setHints] = useState(() => lesson.blocks.filter((block) => block.type === "hint").sort((a, b) => (a.type === "hint" ? a.payload.level : 0) - (b.type === "hint" ? b.payload.level : 0)).map((block) => block.type === "hint" ? block.payload.hint : ""));
  const sourceCitations = lesson.blocks.flatMap((block) => (block.payload.sourceRefs ?? []).map((reference) => ({ title: block.title, reference })));

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
    setAnswer(question?.answerSpec?.expected ?? question?.correctAnswers?.[0] ?? question?.acceptedAnswers?.[0] ?? "");
    setQuestionKind(question?.questionKind ?? "short_answer");
    setAnswerMode(question?.answerMode ?? "short_answer");
    setAcceptedAnswers((question?.acceptedAnswers ?? question?.correctAnswers ?? []).join("\n"));
    setVariantApprovals([]);
    setRequiredEvidenceRefs(question?.requiredEvidenceRefs ?? lesson.evidenceOptions ?? []);
    setRubricText(criteriaText(question?.rubric));
    setConceptMarkdown(lesson.blocks.find((block) => block.type === "markdown")?.payload.markdown ?? "");
    setExampleText(lesson.blocks.find((block) => block.type === "annotation")?.payload.text ?? "");
    setHints(lesson.blocks.filter((block) => block.type === "hint").sort((a, b) => (a.type === "hint" ? a.payload.level : 0) - (b.type === "hint" ? b.payload.level : 0)).map((block) => block.type === "hint" ? block.payload.hint : ""));
  }, [editing, lesson.blocks, lesson.evidenceOptions, lesson.lessonId, question?.acceptedAnswers, question?.answerMode, question?.prompt, question?.questionKind, question?.requiredEvidenceRefs, question?.rubric, question?.answerSpec?.expected, question?.correctAnswers]);

  return (
    <article className="chapter-lesson-card" aria-label={lesson.title}>
      <div className="chapter-lesson-heading">
        <div><h3>{lesson.title}</h3><span className={`chapter-status ${lesson.status}`}>{lesson.status === "approved" ? "已审核" : lesson.status === "needs_review" ? "需复核" : "待审核"}</span></div>
        <button type="button" onClick={() => onFocusSource(lesson)}>回看第 {lesson.sourceLocator.page} 页来源区域</button>
      </div>
      {lesson.reviewIssues.map((issue) => <p className="chapter-issue blocking" role="status" key={`${issue.code}-${issue.message}`}>{issue.message}</p>)}
      <div className="chapter-lesson-generated-label">课程草稿 · 审核前不会进入学生端</div>
      <LessonPlayer document={previewDocument} studentMode />
      {!question && <><p>请核对教程讲解与各环节的原文引用，确认后批准。</p><details><summary>教程原文引用</summary><ul>{sourceCitations.map(({ title, reference }, index) => <li key={index}>{title} · 第 {reference.page} 页 · {reference.quote}</li>)}</ul></details></>}
      {question && (
        <section className="chapter-check-question" aria-label={`检查题：${lesson.title}`}>
          <h4>检查题</h4>
          <p>{question.prompt}</p>
          {lesson.status !== "approved" && <p className="chapter-author-answer">标准答案候选：{primaryAnswer || "尚未补充；发布会被阻止"}</p>}
          {!!suggestedVariants.length && <div className="chapter-review-note"><strong>{variantsConfirmed ? "已由教师确认的答案变体" : "建议答案变体 · 待教师审核"}</strong><p>{variantsConfirmed ? "这些变体由教师明确选择，并已纳入答案标准。" : "AI 建议仅供参考，尚未纳入自动判分；请逐项核验后再决定是否接受。"}</p><ul>{suggestedVariants.map((variant) => <li key={variant}>{variant}</li>)}</ul></div>}
          {question.rubric && <details className="chapter-review-note"><summary>评分依据 · 需教师审核</summary><p>请核对标准与来源是否匹配。保存草稿不会自动批准评分规则。</p><p>{criteriaText(question.rubric) || "请核对答案是否由原文支持，再确认复核。"}</p></details>}
          {!!sourceCitations.length && <details className="chapter-review-citations"><summary>生成内容引用（{sourceCitations.length} 项）</summary><ul>{sourceCitations.map(({ title, reference }, index) => <li key={`${title}-${reference.page}-${reference.sentenceId ?? reference.regionId ?? index}`}><strong>{title}</strong> · 第 {reference.page} 页{reference.quote ? ` · ${reference.quote}` : reference.regionId ? ` · 区域 ${reference.regionId}` : ""}</li>)}</ul></details>}
          {!editing ? <button type="button" disabled={Boolean(busy)} onClick={() => setEditing(true)}>{answer ? "编辑检查题" : "补充检查题"}</button> : (
            <form className="chapter-check-editor" onSubmit={async (event) => {
              event.preventDefault();
              const criteria = rubricText.split("\n").map((item) => item.trim()).filter(Boolean);
              const rubric = { ...question.rubric, ...(criteria.length || question.rubric?.criteria ? { criteria } : {}) };
              const result = await onEdit(lesson, {
                prompt: prompt.trim(), answer: answer.trim(), answerType, questionKind, answerMode,
                acceptedAnswers: [...new Set([...acceptedAnswers.split("\n").map((item) => item.trim()).filter(Boolean), ...variantApprovals])],
                ...(variantApprovals.length ? { teacherVariants: variantApprovals } : {}),
                requiredEvidenceRefs: requiredEvidenceRefs.map((ref) => ({
                  sourceRevisionId: ref.sourceRevisionId,
                  page: ref.page,
                  ...(ref.regionId ? { regionId: ref.regionId } : {}),
                  ...(ref.sentenceId ? { sentenceId: ref.sentenceId } : {}),
                  ...(ref.quote ? { quote: ref.quote } : {}),
                })),
                rubric, ...(question.subject !== "english" ? { conceptMarkdown } : {}), exampleText, hints: hints.length ? hints : undefined, hint: hints[0] ?? "",
                sourceRevisionId: lesson.sourceRevisionId, page: lesson.sourceLocator.page,
              });
              if (result) setEditing(false);
            }}>
              <label>检查题<input value={prompt} onChange={(event) => setPrompt(event.target.value)} required /></label>

              <label>标准答案<input value={answer} onChange={(event) => setAnswer(event.target.value)} required /></label>
              <details className="chapter-advanced"><summary>判分设置与可接受答案</summary><div>
              <label>答案类型<select value={answerType} onChange={(event) => setAnswerType(event.target.value as "text" | "numeric")}><option value="text">文本依据</option><option value="numeric">数字/公式</option></select></label>
              <label>题型<select value={questionKind} onChange={(event) => setQuestionKind(event.target.value as ChapterLessonEditInput["questionKind"])}><option value="word_meaning">词义</option><option value="reference">指代</option><option value="explicit">明示信息</option><option value="inference">有依据推断</option><option value="short_answer">简答</option></select></label>
              <label>判定方式<select value={answerMode} onChange={(event) => setAnswerMode(event.target.value as ChapterLessonEditInput["answerMode"])}><option value="objective">客观判定</option><option value="short_answer">简答复核</option></select></label>
              <label>已确认可接受答案（每行一个）<textarea rows={3} value={acceptedAnswers} onChange={(event) => setAcceptedAnswers(event.target.value)} /></label>
              </div></details>
              {!!suggestedVariants.length && !variantsConfirmed && <fieldset className="chapter-variant-review"><legend>教师选择是否接受建议变体</legend>{suggestedVariants.map((variant) => <label key={variant}><input type="checkbox" checked={variantApprovals.includes(variant)} onChange={() => setVariantApprovals((current) => current.includes(variant) ? current.filter((item) => item !== variant) : [...current, variant])} />确认接受：{variant}</label>)}</fieldset>}
              <fieldset><legend>要求学生选择的原文证据</legend>
                {lesson.evidenceOptions?.length ? lesson.evidenceOptions.map((option) => {
                  const checked = requiredEvidenceRefs.some((ref) => ref.sentenceId === option.sentenceId && ref.regionId === option.regionId && ref.page === option.page);
                  return <label key={`${option.page}-${option.sentenceId ?? option.regionId ?? option.quote}`}><input type="checkbox" checked={checked} onChange={() => setRequiredEvidenceRefs((current) => checked
                    ? current.filter((ref) => !(ref.sentenceId === option.sentenceId && ref.regionId === option.regionId && ref.page === option.page))
                    : [...current, option])} />第 {option.page} 页 · {option.label || option.quote}</label>;
                }) : <p>当前没有可选原文句段；请先补充可引用的句子或区域。</p>}
              </fieldset>
              <label>评分要点（每行一条）<textarea rows={4} value={rubricText} onChange={(event) => setRubricText(event.target.value)} /></label>
              {question.subject !== "english" && <label>概念讲解<textarea rows={3} value={conceptMarkdown} onChange={(event) => setConceptMarkdown(event.target.value)} /></label>}
              <label>来源例题/例句<textarea rows={3} value={exampleText} onChange={(event) => setExampleText(event.target.value)} /></label>
              {hints.map((hint, index) => <label key={index}>第 {index + 1} 级提示<textarea rows={2} value={hint} onChange={(event) => setHints((current) => current.map((item, itemIndex) => itemIndex === index ? event.target.value : item))} /></label>)}
              <div><button type="button" disabled={Boolean(busy)} onClick={() => setEditing(false)}>取消</button><button type="submit" disabled={Boolean(busy)}>{busy === "edit" ? "保存中…" : "保存题目"}</button></div>
            </form>
          )}
        </section>
      )}
      <div className="chapter-review-actions">
        <button type="button" disabled={Boolean(busy)} onClick={() => onReview(lesson, "request_changes")}>{busy === "review" ? "保存中…" : "标记待修改"}</button>
        <button type="button" className="primary" disabled={Boolean(busy) || (Boolean(question) && (!question?.prompt || !(question.answerSpec?.expected || question.correctAnswers?.length || question.acceptedAnswers?.length)))} onClick={() => onReview(lesson, "approve")}>{busy === "review" ? "保存中…" : "确认已复核"}</button>
      </div>
    </article>
  );
}
