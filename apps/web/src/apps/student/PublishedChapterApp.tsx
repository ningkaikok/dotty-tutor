import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";
import { loadPublishedChapter } from "../../api/chapters";
import { useLearnerId } from "../../api/identity";
import { LessonPlayer } from "../../lesson/LessonPlayer";
import type { LessonDocument } from "../../types/lesson";
import type { PublishedChapter } from "../../types/chapter";
import { ChapterAttemptPanel } from "../chapters/ChapterAttemptPanel";
import { useChapterLearningSession } from "../chapters/useChapterLearningSession";
import "../chapters/chapters.css";

export function PublishedChapterApp() {
  const navigate = useNavigate();
  const { chapterId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const publicationId = searchParams.get("publicationId") || undefined;
  const learnerId = useLearnerId();
  const [chapter, setChapter] = useState<PublishedChapter | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    loadPublishedChapter(chapterId, publicationId, controller.signal)
      .then((next) => { if (!controller.signal.aborted) setChapter(next); })
      .catch((requestError) => { if (!controller.signal.aborted) setError(requestError instanceof Error ? requestError.message : "章节加载失败"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [chapterId, publicationId, retryCount]);

  if (loading) return <main className="chapter-shell"><p role="status">正在载入已发布课程…</p></main>;
  if (error || !chapter) return <main className="chapter-shell"><header className="chapter-topbar"><button type="button" onClick={() => navigate("/learn")}>← 学生学习空间</button></header><div className="chapter-error" role="alert"><span>{error || "未找到已发布章节"}</span><button type="button" onClick={() => setRetryCount((value) => value + 1)}>重试</button></div></main>;

  return <PublishedChapterLearning key={`${learnerId}:${chapter.publicationId}`} chapter={chapter} onBack={() => navigate("/learn")} />;
}

function PublishedChapterLearning({ chapter, onBack }: { chapter: PublishedChapter; onBack: () => void }) {
  const { lesson, lessonIndex, lessonCount, draft, busy, error, changeAnswer, changeEvidence, submit, selectLesson } = useChapterLearningSession(chapter);

  if (!lesson) return <main className="chapter-shell"><header className="chapter-topbar"><button type="button" onClick={onBack}>← 学生学习空间</button></header><p>这个已发布课程暂时没有可学习内容。</p></main>;
  const playerDocument: LessonDocument = {
    lessonId: lesson.lessonId,
    title: lesson.title,
    version: lesson.version,
    status: "published" as const,
    sourceUploadId: undefined,
    knowledgePoints: lesson.knowledgePoints,
    blocks: lesson.blocks,
  };

  return (
    <main className="chapter-shell chapter-student-shell">
      <header className="chapter-topbar"><button type="button" onClick={onBack}>← 学生学习空间</button><strong>Dotty · 已发布章节</strong><span>版本 {chapter.version}</span></header>
      <section className="chapter-page-heading"><span className="eyebrow">{chapter.teachingMode === "tutorial" ? "讲解教程" : chapter.subject === "math" ? "数学" : "英语阅读"} · 已审核发布</span><h1>{chapter.title}</h1><p>第 {lessonIndex + 1}/{lessonCount} 课 · 发布版本 {chapter.publicationId}</p></section>
      <div className="chapter-student-nav" aria-label="课程导航">
        {chapter.lessons.map((item, index) => <button type="button" className={index === lessonIndex ? "active" : ""} key={item.lessonId} onClick={() => selectLesson(index)}>第 {index + 1} 课</button>)}
      </div>
      <section className="chapter-student-source" aria-label="课程来源">
        <div><strong>来源第 {lesson.sourceLocator.page} 页</strong><span>依据引用 {lesson.evidenceOptions?.length ?? 0} 个</span></div>
        {lesson.evidenceOptions?.map((option) => option.quote
          ? <blockquote key={`${option.page}-${option.sentenceId ?? option.regionId ?? option.quote}`}>{option.quote}</blockquote>
          : <p key={`${option.page}-${option.regionId ?? option.label}`}>第 {option.page} 页 · {option.label || `原文区域 ${option.regionId ?? "未命名"}`}</p>)}
      </section>
      <section className="chapter-student-player"><div className="chapter-lesson-generated-label">审核后发布的课程内容</div><div className="chapter-course-player"><LessonPlayer document={playerDocument} studentMode /></div></section>
      {lesson.questionPayload?.question && <ChapterAttemptPanel
        lesson={lesson}
        subject={chapter.subject}
        answer={draft.answer}
        evidenceRefs={draft.evidenceRefs}
        result={draft.result ?? null}
        busy={busy}
        error={error}
        onAnswerChange={changeAnswer}
        onEvidenceChange={changeEvidence}
        onSubmit={() => void submit()}
      />}
      <footer className="chapter-student-footer">
        <button type="button" disabled={lessonIndex === 0} onClick={() => selectLesson(lessonIndex - 1)}>上一课</button>
        <button type="button" disabled={lessonIndex >= lessonCount - 1} onClick={() => selectLesson(lessonIndex + 1)}>下一课</button>
      </footer>
    </main>
  );
}
