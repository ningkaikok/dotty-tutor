import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";
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
  return chapterId && chapterId !== "new" ? <ChapterWorkspace chapterId={chapterId} /> : <ChapterCreator />;
}

function ChapterCreator() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [libraries, setLibraries] = useState<LibraryItem[]>([]);
  const { creating, createError, create: createFromHook } = useChapterStudioIndex(false);
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
      <header className="chapter-topbar"><button type="button" onClick={() => navigate("/studio")}>← 我的教材</button><strong>Dotty · 课程编辑</strong></header>
      <section className="chapter-page-heading"><span className="eyebrow">1 · 选择教材内容</span><h1>制作一节课程</h1><p>选择教材页或粘贴原文。保存后生成草稿，确认内容无误再发布。</p></section>
      <div className="chapter-creator-grid chapter-creator-simple">
        <section className="chapter-panel">
          <h2>这节课讲什么？</h2>
          {loadingLibrary ? <p role="status">正在读取已识别教材…</p> : <ChapterSourceForm libraries={libraries} initialUploadId={params.get("uploadId") ?? ""} busy={creating} submitLabel="保存并继续" onSubmit={(value) => void create(value)} />}
          {(error || createError) && <div className="chapter-error" role="alert"><span>{error || createError}</span><button type="button" onClick={() => void refreshLibrary()}>重试读取教材库</button></div>}
          {!loadingLibrary && !libraries.length && <p className="chapter-help">教材库为空时，可以粘贴一页已有 OCR 原文；跨页课程需先将教材加入教材库。</p>}
        </section>

      </div>
    </main>
  );
}

function ChapterWorkspace({ chapterId }: { chapterId: string }) {
  const navigate = useNavigate();
  const { chapter, loading, busy, error, notice, aiJob, reload, generate, startAiGeneration, retryAiGeneration, cancelAiGeneration, revise, edit, review, publish } = useChapterStudio(chapterId);
  const [libraries, setLibraries] = useState<LibraryItem[]>([]);
  const [showRevision, setShowRevision] = useState(false);
  const [showSource, setShowSource] = useState(false);
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
        <button type="button" onClick={() => navigate("/studio")}>← 我的教材</button>
        <strong>Dotty · 课程编辑</strong>
        <button type="button" onClick={() => void reload()} disabled={loading}>刷新</button>
      </header>
      <section className="chapter-page-heading">
        <span className="eyebrow">{chapter.subject === "math" ? "数学章节" : "英语阅读"} · 版本 {chapter.version}</span>
        <h1>{chapter.title}</h1>
        <p>状态：{({ draft: "草稿", needs_review: "需复核", in_review: "审核中", published: "已发布" } as const)[chapter.status]} · 来源修订 {chapter.sourceRevisions.length} 个 · 发布历史 {chapter.publications.length} 个</p>
        <ol className="chapter-steps" aria-label="课程制作进度">
          <li aria-current={!currentLessons.length ? "step" : undefined}>1 生成草稿</li>
          <li aria-current={currentLessons.length && !canPublish && chapter.status !== "published" ? "step" : undefined}>2 复核内容</li>
          <li aria-current={canPublish || chapter.status === "published" ? "step" : undefined}>3 发布课程</li>
        </ol>
        <p className="chapter-help">{!currentLessons.length ? "先生成草稿，再逐节检查题目与答案。" : canPublish ? "内容已复核，可以发布给学生。" : "核对题目、答案和原文依据，确认后再发布。"}</p>
        <div className="chapter-toolbar">

          <button type="button" disabled={Boolean(busy) || aiJobActive} onClick={() => void startAiGeneration()} className="primary">{currentLessons.length ? "重新生成 AI 草稿" : "生成 AI 草稿"}</button>
          <button type="button" className="primary" disabled={Boolean(busy) || aiJobActive || !canPublish} title={canPublish ? "发布经审核课程" : "请解决来源/课程复核项并审核所有检查题"} onClick={() => void publish()}>{busy === "publish" ? "发布中…" : "发布课程"}</button>
          {chapter.publicationId && <button type="button" onClick={() => navigate(`/learn/chapters/${chapter.chapterId}`)}>学生端预览</button>}
        </div>
        <details className="chapter-secondary-actions"><summary>其他制作方式与来源设置</summary><div>
          <button type="button" disabled={Boolean(busy) || aiJobActive || Boolean(chapter.currentLessonIds.length)} onClick={() => void generate()}>{busy === "generate" ? "生成中…" : "模板生成课程草稿"}</button>
          <button type="button" disabled={Boolean(busy) || aiJobActive} onClick={() => setShowRevision((value) => !value)}>修订来源</button>
        </div></details>
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
      <button type="button" className="chapter-source-toggle" aria-expanded={showSource} onClick={() => setShowSource((value) => !value)}>{showSource ? "收起教材原文" : "查看教材原文与页码"}</button>
      <div className={`chapter-review-layout${showSource ? "" : " chapter-content-only"}`}>
        {showSource && <ChapterSourceReview revisions={chapter.sourceRevisions} locator={focusedLocator} />}
        <ChapterLessonReview
          lessons={currentLessons}
          busyLessonId={busy.startsWith("edit:") ? busy.slice(5) : busy.startsWith("review:") ? busy.slice(7) : ""}
          busyAction={aiJobActive ? "locked" : busy.startsWith("edit:") ? "edit" : busy.startsWith("review:") ? "review" : ""}
          onEdit={(lesson, value) => edit(lesson, { ...value, sourceRevisionId: lesson.sourceRevisionId, page: lesson.sourceLocator.page })}
          onReview={(lesson, decision) => void review(lesson, decision)}
          onFocusSource={(lesson) => { setShowSource(true); setFocusedLocator(lesson.sourceLocator); }}
        />
      </div>
      {!!blockers.length && <section className="chapter-panel chapter-blockers"><h2>发布前待处理</h2><ul>{blockers.map((item, index) => <li key={`${item.code}-${item.lessonId ?? "chapter"}-${index}`}>{item.message}</li>)}</ul></section>}
      {!canPublish && currentLessons.length > 0 && blockers.length === 0 && <p className="chapter-help">逐一打开检查题并确认人工复核后，发布按钮才会启用。</p>}
      {chapter.publications.length > 0 && <details className="chapter-panel chapter-history"><summary>查看发布历史</summary><ul>{chapter.publications.map((item) => <li key={item.publicationId}>
        <button type="button" onClick={() => navigate(`/learn/chapters/${chapter.chapterId}?publicationId=${encodeURIComponent(item.publicationId)}`)}>学生端查看版本 {item.version}</button>
      </li>)}</ul></details>}
      {chapter.subject === "english" && <details className="chapter-panel chapter-history"><summary>学生阅读答案复核</summary><ChapterAttemptReview busy={attemptReview.busy} error={attemptReview.error} result={attemptReview.result} onLoad={attemptReview.read} onReview={attemptReview.review} /></details>}
    </main>
  );
}
