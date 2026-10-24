import { Badge, Button, ButtonLink, PageHeader } from "../../components/ui";
import { useState } from "react";
import { Link } from "react-router";
import { useQualityReviewInbox } from "../quality-review/useQualityReviewInbox";
import type { QualityReviewInboxMediaTask } from "../../api/qualityReviews";
import { MaterialDeleteAction } from "./MaterialDeleteAction";
import { MaterialsHeader } from "./MaterialsHeader";
import { useMaterials } from "./useMaterials";
import { MaterialCoursewareSummary } from "./MaterialCoursewareSummary";
import "../chapters/chapters.css";
import "./materials.css";

const statuses: Record<string, string> = { draft: "待生成", in_review: "待复核", needs_review: "需修改", published: "已发布" };
type ReviewItem = ReturnType<typeof useQualityReviewInbox>["items"][number];

function subjectLabel(subject: string) {
  if (subject === "english") return "英语";
  if (subject === "math") return "数学";
  if (subject === "science") return "科学";
  return subject || "未配置学科";
}

/** Keep source review tasks alongside their upload and chapter context in the main library. */
export function MaterialsApp() {
  const { uploads, chapters, courseware, coursewareErrors, coursewareLoading, errors, loading, uploadsAvailable, chaptersAvailable, reload, remove } = useMaterials();
  const [qualityOpen, setQualityOpen] = useState(false);
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [includeReviewed, setIncludeReviewed] = useState(false);
  const [reviewChapterId, setReviewChapterId] = useState("");
  const [reviewSourceKey, setReviewSourceKey] = useState("");
  const globalInbox = useQualityReviewInbox();
  const inbox = useQualityReviewInbox({ chapterId: reviewChapterId || undefined, includeReviewed });
  const query = search.trim().toLocaleLowerCase();
  const uploadById = new Map(uploads.map((item) => [item.uploadId, item]));
  const chapterById = new Map(chapters.map((item) => [item.chapterId, item]));
  const books = uploads.filter((item) => item.materialKind !== "paper")
    .map((upload) => ({ upload, chapters: chapters.filter((chapter) => chapter.uploadId === upload.uploadId).sort((a, b) => (a.pageStart ?? 0) - (b.pageStart ?? 0)) }))
    .filter((book) => book.chapters.length > 0);
  const groupedIds = new Set(books.flatMap((book) => book.chapters.map((chapter) => chapter.chapterId)));
  const tutorials = books.filter((book) => (filter === "all" || filter === "books") && `${book.upload.filename} ${book.chapters.map((chapter) => `${chapter.title} ${subjectLabel(chapter.subject)}`).join(" ")}`.toLocaleLowerCase().includes(query));
  const files = uploads.filter((item) => !books.some((book) => book.upload.uploadId === item.uploadId))
    .filter((item) => item.filename.toLocaleLowerCase().includes(query) && (filter === "all" || (filter === "books" ? item.materialKind === "textbook" : filter === "papers" ? item.materialKind === "paper" : filter === "unknown" && (item.materialKind === "unknown" || !item.materialKind))));
  const courses = chapters.filter((item) => !groupedIds.has(item.chapterId))
    .filter((item) => (filter === "all" || filter === "books") && `${item.title} ${subjectLabel(item.subject)}`.toLocaleLowerCase().includes(query));
  const allReviewGroups = groupReviews(inbox.items, uploadById);
  const selectedGroup = allReviewGroups.find((group) => group.sourceKey === reviewSourceKey);
  const selectedChapterIds = new Set(selectedGroup?.chapterIds ?? []);
  const chapterMatches = (chapterId: string) => !reviewChapterId || reviewChapterId === chapterId;
  const sourceMatchesChapter = (chapterId: string) => !reviewSourceKey || selectedChapterIds.has(chapterId);
  const reviewGroups = allReviewGroups.filter((group) => !reviewSourceKey || group.sourceKey === reviewSourceKey).filter((group) =>
    `${group.title} ${group.items.map((item) => item.externalCaseId).join(" ")}`.toLocaleLowerCase().includes(query));
  const reviewCourses = chapters.filter((item) => ["in_review", "needs_review"].includes(item.status))
    .filter((item) => chapterMatches(item.chapterId) && sourceMatchesChapter(item.chapterId)
      && `${item.title} ${item.uploadId ? uploadById.get(item.uploadId)?.filename ?? "" : ""}`.toLocaleLowerCase().includes(query));
  const mediaTasks = inbox.counts.mediaTasks.filter((item) => chapterMatches(item.chapterId)
    && (!reviewSourceKey || item.sourceKey === reviewSourceKey)
    && `${item.title} ${chapterById.get(item.chapterId)?.title ?? ""} ${item.uploadId ? uploadById.get(item.uploadId)?.filename ?? "" : ""}`.toLocaleLowerCase().includes(query));
  const sourceOptions = allReviewGroups.map((group) => ({ sourceKey: group.sourceKey, title: group.title }));
  const coursewareTasks = (inbox.counts.coursewareTasks ?? []).filter((item) => chapterMatches(item.chapterId)
    && (!reviewSourceKey || item.sourceKey === reviewSourceKey) && item.title.toLocaleLowerCase().includes(query));
  for (const item of inbox.counts.coursewareTasks ?? []) {
    if (!sourceOptions.some((option) => option.sourceKey === item.sourceKey)) sourceOptions.push({ sourceKey: item.sourceKey, title: item.uploadId ? uploadById.get(item.uploadId)?.filename ?? item.title : item.title });
  }
  for (const item of inbox.counts.mediaTasks) {
    if (sourceOptions.some((option) => option.sourceKey === item.sourceKey)) continue;
    sourceOptions.push({ sourceKey: item.sourceKey, title: item.uploadId ? uploadById.get(item.uploadId)?.filename ?? `发布来源 ${item.publicationId}` : `发布来源 ${item.publicationId}` });
  }
  const count = files.length + courses.length + tutorials.length;
  const countsAreCurrent = !loading && !errors.length && !globalInbox.loading && !globalInbox.error && globalInbox.hasCounts && uploadsAvailable && chaptersAvailable;
  const knownCourseCount = chaptersAvailable && !errors.some((message) => message.startsWith("课程暂时无法读取"))
    ? chapters.filter((item) => ["in_review", "needs_review"].includes(item.status)).length
    : null;
  const knownSourceCount = globalInbox.hasCounts && !globalInbox.error ? globalInbox.counts.pendingCount : null;
  const knownMediaCount = globalInbox.hasCounts && !globalInbox.error ? globalInbox.counts.mediaPendingCount : null;
  const pendingLabel = countsAreCurrent
    ? `待处理（${(knownCourseCount ?? 0) + (knownMediaCount ?? 0) + (globalInbox.counts.coursewarePendingCount ?? 0)}）`
    : "待处理（数量暂不可用）";
  const refreshAll = () => { void Promise.all([reload(), globalInbox.refresh(), inbox.refresh()]); };

  return <main className="app-shell materials-shell">
    <MaterialsHeader backTo="/" backLabel="返回首页" subtitle="教材与课程" actions={<Button type="button" disabled={loading || inbox.loading} onClick={refreshAll}>刷新</Button>} />
    <PageHeader className="materials-heading" eyebrow="教材与课程，一个地方" title="我的教材" description="教材课件完成后可直接授课；从教材管理可继续制作。只有未解决的问题需要你处理，试卷继续使用原有练习流程。" actions={<Link className="material-main-action" to="/studio/import">＋ 上传试卷或教材</Link>} />
    <label className="materials-search">查找教材或课程<input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="输入名称、数学或英语" /></label>
    <nav className="materials-filters" aria-label="材料类型">
      {[["all", "全部"], ["books", "教材与课程"], ["papers", "试卷"], ["unknown", "待识别材料"]].map(([value, label]) => <Button type="button" key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</Button>)}
      <Button type="button" aria-pressed={filter === "reviews"} onClick={() => { setFilter("reviews"); setQualityOpen(false); }}>{pendingLabel}</Button>
    </nav>
    {errors.map((message) => <div className="chapter-error" role="alert" key={message}><span>{message}</span><Button type="button" onClick={() => void reload()}>重试</Button></div>)}
    {inbox.error && <div className="chapter-error" role="alert"><span>待审列表无法读取：{inbox.error}；已有教材与课程仍可继续使用。</span><Button type="button" onClick={() => void inbox.refresh()}>重试待审列表</Button></div>}
    {loading && <p role="status">正在读取教材与课程…</p>}
    {filter === "reviews" && inbox.loading && <p role="status">正在读取待我复核的来源案例…</p>}
    {filter === "reviews" && <section aria-label="来源复核任务">
      <div className="materials-review-heading"><div><h2>待处理</h2><p>这里显示需要处理的制作问题；来源质量抽检为可选项。</p></div>
        <label><input type="checkbox" checked={includeReviewed} onChange={(event) => setIncludeReviewed(event.target.checked)} />显示已复核案例</label>
      </div>
      <div className="materials-review-filters">
        <label>按章节筛选<select value={reviewChapterId} onChange={(event) => setReviewChapterId(event.target.value)}>
          <option value="">全部章节</option>{chapters.map((item) => <option key={item.chapterId} value={item.chapterId}>{item.title}</option>)}
        </select></label>
        <label>按教材或来源筛选<select value={reviewSourceKey} onChange={(event) => setReviewSourceKey(event.target.value)}>
          <option value="">全部教材与来源</option>{sourceOptions.map((group, index) => <option key={group.sourceKey} value={group.sourceKey}>{group.title}{sourceOptions.filter((candidate) => candidate.title === group.title).length > 1 ? ` · 来源 ${index + 1}` : ""}</option>)}
        </select></label>
      </div>
      {inbox.hasCounts && !inbox.error && <p className="materials-review-counts" role="status">课程：{knownCourseCount ?? "暂不可用"} 章待复核；课件：{inbox.counts.coursewarePendingCount ?? 0} 份待处理；视频：{inbox.counts.mediaPendingCount} 项待审（正在渲染的视频不计入）。</p>}
      {!inbox.loading && !inbox.error && chaptersAvailable && !reviewCourses.length && !mediaTasks.length && !coursewareTasks.length && <section className="chapter-panel materials-empty"><h3>{query ? "没有找到匹配的制作问题" : "当前没有需要你处理的制作问题"}</h3><p>来源评测记录保留在下面的可选抽检中，无需逐条确认即可使用课件。</p></section>}
      {reviewCourses.length > 0 && <section className="materials-review-task-section" aria-label="课程待复核任务"><h3>课程待复核（{reviewCourses.length}）</h3><ul className="materials-review-task-list">{reviewCourses.map((item) => <li key={item.chapterId}><span>{item.title} · {statuses[item.status] ?? "待复核"}</span><Link to={`/studio/chapters/${item.chapterId}`}>继续课程复核</Link></li>)}</ul></section>}
      <details open={qualityOpen} onToggle={(event) => setQualityOpen(event.currentTarget.open)}><summary>来源质量抽检（{inbox.counts.pendingCount} 项，可选）</summary><p>{inbox.hasCounts ? `${inbox.counts.pendingCount} 项待抽检 · ${inbox.counts.reviewedCount} 项已复核 · ${inbox.counts.unlinkedCount} 项尚未关联章节。` : "抽检记录数量暂不可用。"}这些记录用于可选的来源质量抽检，不影响课件生成或使用。</p>
      <section className="materials-grid materials-source-review-grid" aria-label="按教材或来源分组的复核任务">
        {reviewGroups.map((group) => <article className="chapter-panel material-card material-source-review-card" key={group.sourceKey}>
          <header className="material-source-review-heading"><span className="material-kind">{group.chapterIds.length ? "教材来源复核" : "未关联教材的来源包"}</span><span className="material-source-review-total">{group.items.length} 项案例</span></header>
          <h3>{group.title}</h3>
          {group.uploadId && !group.chapterIds.length && <p>已登记来源文件，尚未关联到当前章节；可直接继续复核。</p>}
          {group.chapterIds.length > 0 && <p>关联章节：{group.chapterIds.map((id) => chapterById.get(id)?.title ?? id).join("、")}</p>}
          <ul className="material-source-review-list" aria-label={`${group.title} 来源案例`}>{group.items.map((item) => <li key={item.caseId}>
            <div className="material-source-review-info"><strong>{item.externalCaseId}</strong><span>{subjectLabel(item.subject)} · PDF 第 {item.sourcePage} 页</span><Badge tone={item.reviewStatus === "human_accepted" ? "success" : item.reviewStatus === "human_rejected" || item.reviewStatus === "needs_changes" ? "warning" : "neutral"}>{reviewStatus(item.reviewStatus)}</Badge></div>
            <div className="material-source-review-action"><ButtonLink to={reviewHref(item)} aria-label={`打开来源复核 ${item.externalCaseId}`}>打开来源复核<span aria-hidden="true">→</span></ButtonLink></div>
          </li>)}</ul>
        </article>)}
      </section></details>
      {coursewareTasks.length > 0 && <section className="materials-review-task-section" aria-label="互动课件待审任务"><h3>互动课件逐页复核（{coursewareTasks.length}）</h3><ul className="materials-review-task-list">{coursewareTasks.map((item) => <li key={item.coursewareId}><span>{item.title} · {item.pageCount} 页 · {item.pendingPageCount} 页待复核</span><Link to={`/studio/courseware/${encodeURIComponent(item.coursewareId)}`}>打开课件逐页复核</Link></li>)}</ul></section>}
      {mediaTasks.length > 0 && <section className="materials-review-task-section" aria-label="教学短视频待审任务"><h3>教学短视频待审（{mediaTasks.filter((item) => item.status !== "rendering").length}）</h3><ul className="materials-review-task-list">{mediaTasks.map((item) => <li key={item.mediaId}>
        <span>{item.title} · {chapterById.get(item.chapterId)?.title ?? item.chapterId} · 视频修订 v{item.version} · 发布快照 {item.publicationId} · {item.status === "rendering" ? "正在渲染" : "待审核"}</span>
        <Link to={mediaReviewHref(item)}>打开视频审校</Link>
      </li>)}</ul></section>}
    </section>}
    {filter !== "reviews" && !loading && count === 0 && <section className="chapter-panel materials-empty"><h2>{query || filter !== "all" ? "没有找到匹配的材料" : errors.length ? "部分内容暂时无法读取" : "从一本教材开始"}</h2><p>{query || filter !== "all" ? "试试其他名称，或切换材料类型。" : errors.length ? "请重试读取；已有文件与课程不会因此被删除。" : "上传 PDF 或图片，系统自动识别。文件和课程都会显示在这里。"}</p>{!query && filter === "all" && <Link to="/studio/import">上传第一份材料 →</Link>}</section>}
    {filter !== "reviews" && <section className="materials-grid" aria-label="教材与课程列表">
      {tutorials.map(({ upload, chapters: allChapters }) => {
        const current = allChapters.filter((chapter) => chapter.teachingMode === "tutorial");
        const contents = current.length ? current : allChapters;
        const history = current.length ? allChapters.filter((chapter) => chapter.teachingMode !== "tutorial") : [];
        return <article className="chapter-panel material-card" key={`tutorial-${upload.uploadId}`}>
        <span className="material-kind">教材课件</span><h2>{upload.filename}</h2><p>{contents.length} 章</p>
        {current.length > 0 && <MaterialCoursewareSummary chapters={current} documents={courseware} loading={coursewareLoading} failed={coursewareErrors} onReload={refreshAll} />}
        <Link className="material-main-action" aria-label={`管理教材 · ${upload.filename}`} to={`/studio/courseware?uploadId=${encodeURIComponent(upload.uploadId)}`}>进入教材管理 →</Link>
        {contents.some((chapter) => chapter.currentLessonIds.length > 0) && <details><summary>旧版课程内容</summary><p>{contents.length} 章 · {contents.reduce((total, chapter) => total + chapter.currentLessonIds.length, 0)} 节旧版课程内容</p></details>}
        <details><summary>查看教程目录</summary><ol>{contents.map((chapter) => <li key={chapter.chapterId}>
          {chapter.teachingMode === "tutorial" ? <strong>{chapter.title}</strong> : <Link to={`/studio/chapters/${chapter.chapterId}`}>{chapter.title}</Link>}<span> · {chapter.teachingMode === "tutorial" ? `教材第 ${chapter.pageStart ?? "?"} 页起` : statuses[chapter.status] ?? "待复核"}</span>{chapter.teachingMode === "tutorial" && <Link to={`/studio/courseware?chapterId=${encodeURIComponent(chapter.chapterId)}`} aria-label={`管理章节课件 · ${chapter.title}`}>管理课件</Link>}
          {chapter.teachingMode !== "tutorial" && <span> · 旧版练习草稿</span>}
          {chapter.publicationId && <Link to={`/learn/chapters/${chapter.chapterId}`}> 学习本章</Link>}
          <MaterialDeleteAction name={chapter.title} onDelete={() => remove("course", chapter.chapterId)} />
        </li>)}</ol></details>
        {history.length > 0 && <details><summary>旧版练习草稿（{history.length} 章）</summary><ol>{history.map((chapter) => <li key={chapter.chapterId}><Link to={`/studio/chapters/${chapter.chapterId}`}>{chapter.title}</Link><span> · {statuses[chapter.status] ?? "待复核"}</span>{chapter.publicationId && <Link to={`/learn/chapters/${chapter.chapterId}`}> 查看旧发布版本</Link>}<MaterialDeleteAction name={chapter.title} onDelete={() => remove("course", chapter.chapterId)} /></li>)}</ol></details>}
        {upload.questionCount > 0 && <Link to={`/studio/import?uploadId=${encodeURIComponent(upload.uploadId)}`}>查看已有练习 →</Link>}
        <MaterialDeleteAction name={upload.filename} onDelete={() => remove("upload", upload.uploadId)} />
      </article>; })}
      {courses.map((item) => <article className="chapter-panel material-card" key={`course-${item.chapterId}`}><span className="material-kind">{item.teachingMode === "tutorial" ? `${subjectLabel(item.subject)}教材章节` : item.subject === "english" ? "英语阅读" : item.subject === "math" ? "数学课程" : subjectLabel(item.subject)}</span><h2>{item.title}</h2>{item.teachingMode === "tutorial" && <MaterialCoursewareSummary chapters={[item]} documents={courseware} loading={coursewareLoading} failed={coursewareErrors} onReload={refreshAll} />}{item.currentLessonIds.length > 0 && <><p>{statuses[item.status] ?? "待复核"} · {item.currentLessonIds.length} 节旧版内容</p><Link className="material-main-action" to={`/studio/chapters/${item.chapterId}`}>{item.status === "published" ? "查看课程" : "继续复核"} →</Link></>}{item.teachingMode === "tutorial" && <Link aria-label={`管理章节课件 · ${item.title}`} to={`/studio/courseware?chapterId=${encodeURIComponent(item.chapterId)}`}>管理课件</Link>}{item.publicationId && <Link to={`/learn/chapters/${item.chapterId}`}>查看学生版本</Link>}<MaterialDeleteAction name={item.title} onDelete={() => remove("course", item.chapterId)} /></article>)}
      {files.map((item) => <article className="chapter-panel material-card" key={`upload-${item.uploadId}`}><span className="material-kind">{item.materialKind === "paper" ? "已上传试卷" : item.materialKind === "textbook" ? "已上传教材" : "待识别材料"}</span><h2>{item.filename}</h2><p>{item.pageCount ?? "?"} 页 · {item.questionCount} 道已生成练习</p>{item.questionCount > 0 && <Link className="material-main-action" to={`/studio/import?uploadId=${encodeURIComponent(item.uploadId)}`}>查看练习 →</Link>}{item.materialKind !== "paper" && <Link aria-label={`管理教材 · ${item.filename}`} to={`/studio/courseware?uploadId=${encodeURIComponent(item.uploadId)}`}>进入教材管理</Link>}<MaterialDeleteAction name={item.filename} onDelete={() => remove("upload", item.uploadId)} /></article>)}
    </section>}
    <details className="materials-tools"><summary>教学工具</summary><nav aria-label="教学工具"><Button onClick={() => { setFilter("reviews"); setQualityOpen(true); }}>来源质量抽检（{knownSourceCount ?? "暂不可用"}）</Button><Link to="/studio/prompts">教学策略</Link><Link to="/studio/metrics">模型调用指标</Link><Link to="/studio/dependency-preflight">环境检查</Link></nav></details>
  </main>;
}

function groupReviews(items: ReviewItem[], uploadById: Map<string, { filename: string }>) {
  const groups = new Map<string, { sourceKey: string; title: string; uploadId: string | null; items: ReviewItem[]; chapterIds: string[] }>();
  for (const item of items) {
    const group = groups.get(item.sourceKey) ?? {
      sourceKey: item.sourceKey,
      title: item.uploadId ? uploadById.get(item.uploadId)?.filename ?? item.sourceTitle : item.sourceTitle,
      uploadId: item.uploadId,
      items: [],
      chapterIds: [],
    };
    group.items.push(item);
    group.chapterIds = [...new Set([...group.chapterIds, ...item.chapterIds])];
    if (!group.uploadId && item.uploadId) group.uploadId = item.uploadId;
    groups.set(item.sourceKey, group);
  }
  return [...groups.values()];
}

function reviewHref(item: ReviewItem): string {
  if (item.chapterIds.length) {
    const query = new URLSearchParams({ sourceReviewPackageId: item.packageId, sourceReviewCaseId: item.caseId });
    return `/studio/chapters/${encodeURIComponent(item.chapterIds[0])}?${query}`;
  }
  const query = new URLSearchParams({ packageId: item.packageId, caseId: item.caseId, returnTo: "/studio" });
  if (item.uploadId) query.set("uploadId", item.uploadId);
  return `/studio/quality-review?${query}`;
}

function mediaReviewHref(item: QualityReviewInboxMediaTask): string {
  const query = new URLSearchParams({ mediaReviewPublicationId: item.publicationId, mediaReviewLessonId: item.lessonId });
  return `/studio/chapters/${encodeURIComponent(item.chapterId)}?${query}`;
}

function reviewStatus(status: string): string {
  if (status === "needs_changes") return "待复核 · 需补充";
  if (["pending_source_verification", "pending_human_review", "decision_recorded_pending_source"].includes(status)) return "待复核";
  if (status === "human_accepted") return "已接受";
  if (status === "human_rejected") return "已拒绝";
  return status;
}
