import { useState } from "react";
import { Link } from "react-router";
import { useMaterials } from "./useMaterials";
import { MaterialDeleteAction } from "./MaterialDeleteAction";
import { MaterialsHeader } from "./MaterialsHeader";
import "../chapters/chapters.css";
import "./materials.css";

const statuses: Record<string, string> = { draft: "待生成", in_review: "待复核", needs_review: "需修改", published: "已发布" };

/** One library for uploaded sources and source-bound courses, without moving either record. */
export function MaterialsApp() {
  const { uploads, chapters, errors, loading, reload, remove } = useMaterials();
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const query = search.trim().toLocaleLowerCase();
  const books = uploads.filter((item) => item.materialKind !== "paper").map((upload) => ({ upload, chapters: chapters.filter((chapter) => chapter.uploadId === upload.uploadId).sort((a, b) => (a.pageStart ?? 0) - (b.pageStart ?? 0)) })).filter((book) => book.chapters.length > 0);
  const groupedIds = new Set(books.flatMap((book) => book.chapters.map((chapter) => chapter.chapterId)));
  const tutorials = books.filter((book) => (filter === "all" || filter === "books") && `${book.upload.filename} ${book.chapters.map((chapter) => `${chapter.title} ${chapter.subject === "english" ? "英语" : "数学"}`).join(" ")}`.toLocaleLowerCase().includes(query));
  const files = uploads.filter((item) => !books.some((book) => book.upload.uploadId === item.uploadId)).filter((item) => item.filename.toLocaleLowerCase().includes(query) && (filter === "all" || (filter === "books" ? item.materialKind === "textbook" : filter === "papers" ? item.materialKind === "paper" : item.materialKind === "unknown" || !item.materialKind)));
  const courses = chapters.filter((item) => !groupedIds.has(item.chapterId)).filter((item) => (filter === "all" || filter === "books") && `${item.title} ${item.subject === "english" ? "英语 阅读" : "数学"}`.toLocaleLowerCase().includes(query));
  const count = files.length + courses.length + tutorials.length;
  return <main className="app-shell materials-shell">
    <MaterialsHeader backTo="/" backLabel="返回首页" subtitle="教材与课程" actions={<button type="button" disabled={loading} onClick={() => void reload()}>刷新</button>} />
    <section className="materials-heading"><div><span className="eyebrow">教材与课程，一个地方</span><h1>我的教材</h1><p>上传试卷或教材，自动识别类型；一本教材制作一个讲解教程，章节在教程内管理。当前最多处理前 5 章。</p></div>
      <Link className="material-main-action" to="/studio/import">＋ 上传试卷或教材</Link>
    </section>
    <label className="materials-search">查找教材或课程<input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="输入名称、数学或英语" /></label>
    <nav className="materials-filters" aria-label="材料类型">{[["all", "全部"], ["books", "教材与课程"], ["papers", "试卷"], ["unknown", "待识别材料"]].map(([value, label]) => <button type="button" key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>)}</nav>
    {errors.map((message) => <div className="chapter-error" role="alert" key={message}><span>{message}</span><button type="button" onClick={() => void reload()}>重试</button></div>)}
    {loading && <p role="status">正在读取教材与课程…</p>}
    {!loading && count === 0 && <section className="chapter-panel materials-empty"><h2>{query || filter !== "all" ? "没有找到匹配的材料" : errors.length ? "部分内容暂时无法读取" : "从一本教材开始"}</h2><p>{query || filter !== "all" ? "试试其他名称，或切换材料类型。" : errors.length ? "请重试读取；已有文件与课程不会因此被删除。" : "上传 PDF 或图片，系统自动识别。文件和课程都会显示在这里。"}</p>{!query && filter === "all" && <Link to="/studio/import">上传第一份材料 →</Link>}</section>}
    <section className="materials-grid" aria-label="教材与课程列表">
      {tutorials.map(({ upload, chapters: allChapters }) => {
        const current = allChapters.filter((chapter) => chapter.teachingMode === "tutorial");
        const contents = current.length ? current : allChapters;
        const history = current.length ? allChapters.filter((chapter) => chapter.teachingMode !== "tutorial") : [];
        return <article className="chapter-panel material-card" key={`tutorial-${upload.uploadId}`}>
        <span className="material-kind">教材教程</span><h2>{upload.filename}</h2><p>{contents.length} 章 · {contents.reduce((total, chapter) => total + chapter.currentLessonIds.length, 0)} 节内容 · {contents.every((chapter) => chapter.status === "published") ? "已发布" : "待复核"}</p>
        <p>章节在教程目录内管理，分别复核后发布。</p>
        <details><summary>查看教程目录</summary><ol>{contents.map((chapter) => <li key={chapter.chapterId}>
          <Link to={`/studio/chapters/${chapter.chapterId}`}>{chapter.title}</Link><span> · {statuses[chapter.status] ?? "待复核"}</span>
          {chapter.teachingMode !== "tutorial" && <span> · 旧版练习草稿</span>}
          {chapter.publicationId && <Link to={`/learn/chapters/${chapter.chapterId}`}> 学习本章</Link>}
          <MaterialDeleteAction name={chapter.title} onDelete={() => remove("course", chapter.chapterId)} />
        </li>)}</ol></details>
        {history.length > 0 && <details><summary>旧版练习草稿（{history.length} 章）</summary><ol>{history.map((chapter) => <li key={chapter.chapterId}><Link to={`/studio/chapters/${chapter.chapterId}`}>{chapter.title}</Link><span> · {statuses[chapter.status] ?? "待复核"}</span>{chapter.publicationId && <Link to={`/learn/chapters/${chapter.chapterId}`}> 查看旧发布版本</Link>}<MaterialDeleteAction name={chapter.title} onDelete={() => remove("course", chapter.chapterId)} /></li>)}</ol></details>}
        {contents.some((chapter) => chapter.teachingMode !== "tutorial") && <Link to={`/studio/chapters/new?uploadId=${encodeURIComponent(upload.uploadId)}`}>制作讲解教程</Link>}
        {upload.questionCount > 0 && <Link to={`/studio/import?uploadId=${encodeURIComponent(upload.uploadId)}`}>查看已有练习 →</Link>}
        <MaterialDeleteAction name={upload.filename} onDelete={() => remove("upload", upload.uploadId)} />
      </article>; })}
      {courses.map((item) => <article className="chapter-panel material-card" key={`course-${item.chapterId}`}><span className="material-kind">{item.teachingMode === "tutorial" ? "教程章节" : item.subject === "english" ? "英语阅读" : "数学课程"}</span><h2>{item.title}</h2><p>{statuses[item.status] ?? "待复核"} · {item.currentLessonIds.length} 节内容</p><Link className="material-main-action" to={`/studio/chapters/${item.chapterId}`}>{item.status === "published" ? "查看课程" : item.currentLessonIds.length ? "继续复核" : "制作课程"} →</Link>{item.publicationId && <Link to={`/learn/chapters/${item.chapterId}`}>查看学生版本</Link>}<MaterialDeleteAction name={item.title} onDelete={() => remove("course", item.chapterId)} /></article>)}
      {files.map((item) => <article className="chapter-panel material-card" key={`upload-${item.uploadId}`}><span className="material-kind">{item.materialKind === "paper" ? "已上传试卷" : item.materialKind === "textbook" ? "已上传教材" : "待识别材料"}</span><h2>{item.filename}</h2><p>{item.pageCount ?? "?"} 页 · {item.questionCount} 道已生成练习</p>{item.questionCount > 0 && <Link className="material-main-action" to={`/studio/import?uploadId=${encodeURIComponent(item.uploadId)}`}>查看练习 →</Link>}{item.materialKind !== "paper" && <Link to={`/studio/chapters/new?uploadId=${encodeURIComponent(item.uploadId)}`}>自动制作课程</Link>}<MaterialDeleteAction name={item.filename} onDelete={() => remove("upload", item.uploadId)} /></article>)}
    </section>
    <details className="materials-tools"><summary>教学工具</summary><nav aria-label="教学工具"><Link to="/studio/prompts">教学策略</Link><Link to="/studio/metrics">模型调用指标</Link><Link to="/studio/dependency-preflight">环境检查</Link></nav></details>
  </main>;
}
