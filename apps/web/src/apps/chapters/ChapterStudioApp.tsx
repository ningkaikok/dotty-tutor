import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { loadLibrary } from "../../api/textbooks";
import type { ChapterSource, ChapterSourceLocator, ChapterSubject } from "../../types/chapter";
import type { LibraryItem } from "../../types/textbook";
import { ChapterLessonReview } from "./ChapterLessonReview";
import { ChapterAttemptReview } from "./ChapterAttemptReview";
import { ChapterSourceForm } from "./ChapterSourceForm";
import { ChapterSourceReview } from "./ChapterSourceReview";
import { useChapterStudio } from "./useChapterStudio";
import { useChapterAttemptReview } from "./useChapterAttemptReview";
import { useChapterStudioIndex } from "./useChapterStudioIndex";
import "./chapters.css";

export function ChapterStudioApp() {
  const { chapterId } = useParams();
  return chapterId ? <ChapterWorkspace chapterId={chapterId} /> : <ChapterCreator />;
}

function ChapterCreator() {
  const navigate = useNavigate();
  const [libraries, setLibraries] = useState<LibraryItem[]>([]);
  const { chapters, loadingChapters, chapterListError, creating, createError, reload, create: createFromHook } = useChapterStudioIndex();
  const [loadingLibrary, setLoadingLibrary] = useState(true);
  const [error, setError] = useState("");

  const refreshLibrary = async () => {
    setLoadingLibrary(true);
    setError("");
    try {
      setLibraries(await loadLibrary());
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "教材库读取失败");
    } finally {
      setLoadingLibrary(false);
    }
  };

  useEffect(() => { void refreshLibrary(); }, []);

  const create = async (value: { title: string; subject: ChapterSubject; source: ChapterSource }) => {
    setError("");
    const chapter = await createFromHook(value);
    if (chapter) navigate(`/studio/chapters/${chapter.chapterId}`);
  };

  return (
    <main className="chapter-shell">
      <header className="chapter-topbar"><button type="button" onClick={() => navigate("/studio")}>← 返回教材工作台</button><strong>Dotty · 章节课程工作台</strong></header>
      <section className="chapter-page-heading"><span className="eyebrow">来源关联互动课程</span><h1>从来源页制作互动章节</h1><p>选择已识别的教材页段，或粘贴一页 OCR 原文。生成内容先进入教师复核，学生端只读取发布版本。</p></section>
      <section className="chapter-panel chapter-existing-list" aria-label="已创建章节">
        <div className="chapter-list-heading"><h2>已创建章节</h2><button type="button" disabled={loadingChapters} onClick={() => void reload()}>{loadingChapters ? "正在刷新…" : "刷新列表"}</button></div>
        {chapterListError && <div className="chapter-error" role="alert"><span>{chapterListError}</span><button type="button" onClick={() => void reload()}>重试</button></div>}
        {loadingChapters && <p role="status">正在读取章节列表…</p>}
        {!loadingChapters && !chapters.length && !chapterListError && <p>还没有章节课程。创建后可以从这里继续复核。</p>}
        {!!chapters.length && <ul className="chapter-existing-items">{chapters.map((item) => <li key={item.chapterId}>
          <button type="button" onClick={() => navigate(`/studio/chapters/${item.chapterId}`)}><strong>{item.title}</strong><span>{item.subject === "math" ? "数学" : "英语阅读"} · 版本 {item.version} · {item.status}</span></button>
        </li>)}</ul>}
      </section>
      <div className="chapter-creator-grid">
        <section className="chapter-panel">
          <h2>章节与来源范围</h2>
          {loadingLibrary ? <p role="status">正在读取已识别教材…</p> : <ChapterSourceForm libraries={libraries} busy={creating} submitLabel="创建章节草稿" onSubmit={(value) => void create(value)} />}
          {(error || createError) && <div className="chapter-error" role="alert"><span>{error || createError}</span><button type="button" onClick={() => void refreshLibrary()}>重试读取教材库</button></div>}
          {!loadingLibrary && !libraries.length && <p className="chapter-help">教材库为空时，可以粘贴一页已有 OCR 原文；跨页课程需先将教材加入教材库。</p>}
        </section>
        <aside className="chapter-panel chapter-gates"><h2>发布复核门槛</h2><ul><li>逐页确认来源原文、页码和区域归属。</li><li>缺页、缺条件、错图或不可读页需要先修订来源。</li><li>概念、例式、提示和检查题都需教师确认。</li><li>发布后旧版本和历史作答继续可追溯。</li></ul></aside>
      </div>
    </main>
  );
}

function ChapterWorkspace({ chapterId }: { chapterId: string }) {
  const navigate = useNavigate();
  const { chapter, loading, busy, error, notice, aiJob, reload, generate, startAiGeneration, retryAiGeneration, cancelAiGeneration, revise, edit, review, publish } = useChapterStudio(chapterId);
  const [libraries, setLibraries] = useState<LibraryItem[]>([]);
  const [showRevision, setShowRevision] = useState(false);
  const [libraryError, setLibraryError] = useState("");
  const [focusedLocator, setFocusedLocator] = useState<ChapterSourceLocator | undefined>();
  const attemptReview = useChapterAttemptReview(chapterId);

  useEffect(() => {
    loadLibrary().then(setLibraries).catch((requestError) => setLibraryError(requestError instanceof Error ? requestError.message : "教材库读取失败"));
  }, []);

  if (loading && !chapter) return <main className="chapter-shell"><p role="status">正在读取章节…</p></main>;
  if (!chapter) return <main className="chapter-shell"><div className="chapter-error" role="alert">{error || "章节不存在"}<button type="button" onClick={() => void reload()}>重试</button></div></main>;

  const source = chapter.sourceRevisions[chapter.sourceRevisions.length - 1];
  const currentLessons = chapter.lessons.filter((lesson) => chapter.currentLessonIds.includes(lesson.lessonId));
  const aiJobActive = aiJob?.status === "queued" || aiJob?.status === "running";
  const blockers = [
    ...chapter.reviewIssues,
    ...(source?.issues ?? []),
    ...currentLessons.flatMap((lesson) => lesson.reviewIssues),
  ];
  const canPublish = currentLessons.length > 0 && currentLessons.every((lesson) => lesson.status === "approved") && blockers.length === 0;

  const saveRevision = async (value: Parameters<NonNullable<React.ComponentProps<typeof ChapterSourceForm>["onSubmit"]>>[0]) => {
    const next = await revise(value.source);
    if (next) setShowRevision(false);
  };

  return (
    <main className="chapter-shell">
      <header className="chapter-topbar">
        <button type="button" onClick={() => navigate("/studio/chapters")}>← 章节列表</button>
        <strong>Dotty · 章节课程工作台</strong>
        <button type="button" onClick={() => void reload()} disabled={loading}>刷新</button>
      </header>
      <section className="chapter-page-heading">
        <span className="eyebrow">{chapter.subject === "math" ? "数学章节" : "英语阅读"} · 版本 {chapter.version}</span>
        <h1>{chapter.title}</h1>
        <p>状态：{({ draft: "草稿", needs_review: "需复核", in_review: "审核中", published: "已发布" } as const)[chapter.status]} · 来源修订 {chapter.sourceRevisions.length} 个 · 发布历史 {chapter.publications.length} 个</p>
        <div className="chapter-toolbar">
          <button type="button" disabled={Boolean(busy) || aiJobActive} onClick={() => setShowRevision((value) => !value)}>修订来源</button>
          <button type="button" disabled={Boolean(busy) || aiJobActive || Boolean(chapter.currentLessonIds.length)} onClick={() => void generate()}>{busy === "generate" ? "生成中…" : "模板生成课程草稿"}</button>
          <button type="button" disabled={Boolean(busy) || aiJobActive} onClick={() => void startAiGeneration()}>AI 生成来源约束草稿</button>
          <button type="button" className="primary" disabled={Boolean(busy) || aiJobActive || !canPublish} title={canPublish ? "发布经审核课程" : "请解决来源/课程复核项并审核所有检查题"} onClick={() => void publish()}>{busy === "publish" ? "发布中…" : "发布课程"}</button>
          {chapter.publicationId && <button type="button" onClick={() => navigate(`/learn/chapters/${chapter.chapterId}`)}>学生端预览</button>}
        </div>
        {aiJob && <div className="chapter-ai-job" role="status">
          <span>AI 来源约束草稿：{({ queued: "排队中", running: "生成中", succeeded: "已完成", failed: "失败", cancelled: "已取消" } as const)[aiJob.status]} · {Math.max(0, Math.min(100, Math.round(aiJob.progress)))}%{aiJob.message ? ` · ${aiJob.message}` : ""}</span>
          {aiJobActive && <button type="button" disabled={Boolean(busy)} onClick={() => void cancelAiGeneration()}>取消生成</button>}
          {aiJob.status === "failed" && <button type="button" disabled={Boolean(busy)} onClick={() => void retryAiGeneration()}>重试生成</button>}
        </div>}
      </section>
      {error && <div className="chapter-error" role="alert"><span>{error}</span><button type="button" onClick={() => void reload()}>重试读取</button></div>}
      {notice && <p className="chapter-notice" role="status">{notice}</p>}
      {libraryError && <p className="chapter-warning" role="status">{libraryError}；你仍可回看已有来源。</p>}
      {showRevision && <section className="chapter-panel"><h2>添加来源修订</h2><p>修订只标记相关草稿需要复核，已发布版本仍保留原快照。</p><ChapterSourceForm key={source?.sourceRevisionId} libraries={libraries} initialTitle={chapter.title} initialSubject={chapter.subject} busy={busy === "revise"} submitLabel="保存来源修订" onSubmit={(value) => void saveRevision(value)} /></section>}
      <div className="chapter-review-layout">
        <ChapterSourceReview revisions={chapter.sourceRevisions} locator={focusedLocator} />
        <ChapterLessonReview
          lessons={currentLessons}
          busyLessonId={busy.startsWith("edit:") ? busy.slice(5) : busy.startsWith("review:") ? busy.slice(7) : ""}
          busyAction={aiJobActive ? "locked" : busy.startsWith("edit:") ? "edit" : busy.startsWith("review:") ? "review" : ""}
          onEdit={(lesson, value) => edit(lesson, { ...value, sourceRevisionId: lesson.sourceRevisionId, page: lesson.sourceLocator.page })}
          onReview={(lesson, decision) => void review(lesson, decision)}
          onFocusSource={(lesson) => setFocusedLocator(lesson.sourceLocator)}
        />
      </div>
      {!!blockers.length && <section className="chapter-panel chapter-blockers"><h2>发布前待处理</h2><ul>{blockers.map((item, index) => <li key={`${item.code}-${item.lessonId ?? "chapter"}-${index}`}>{item.message}</li>)}</ul></section>}
      {!canPublish && currentLessons.length > 0 && blockers.length === 0 && <p className="chapter-help">逐一打开检查题并确认人工复核后，发布按钮才会启用。</p>}
      {chapter.publications.length > 0 && <section className="chapter-panel"><h2>不可变发布历史</h2><ul>{chapter.publications.map((item) => <li key={item.publicationId}>
        <button type="button" onClick={() => navigate(`/learn/chapters/${chapter.chapterId}?publicationId=${encodeURIComponent(item.publicationId)}`)}>学生端查看版本 {item.version}</button>
        <span> · 来源修订 {item.sourceRevisionId} · {item.publicationId}</span>
      </li>)}</ul></section>}
      {chapter.subject === "english" && <ChapterAttemptReview busy={attemptReview.busy} error={attemptReview.error} result={attemptReview.result} onLoad={attemptReview.read} onReview={attemptReview.review} />}
    </main>
  );
}
