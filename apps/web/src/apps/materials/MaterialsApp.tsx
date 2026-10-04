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
  const files = uploads.filter((item) => item.filename.toLocaleLowerCase().includes(query) && (filter === "all" || (filter === "books" ? item.materialKind === "textbook" : filter === "papers" ? item.materialKind === "paper" : item.materialKind === "unknown" || !item.materialKind)));
  const courses = chapters.filter((item) => (filter === "all" || filter === "books") && `${item.title} ${item.subject === "english" ? "英语 阅读" : "数学"}`.toLocaleLowerCase().includes(query));
  const count = files.length + courses.length;
  return <main className="app-shell materials-shell">
    <MaterialsHeader backTo="/" backLabel="返回首页" subtitle="教材与课程" actions={<button type="button" disabled={loading} onClick={() => void reload()}>刷新</button>} />
    <section className="materials-heading"><div><span className="eyebrow">教材与课程，一个地方</span><h1>我的教材</h1><p>上传试卷或教材，自动识别类型；教材最多制作前 5 章课程。</p></div>
      <Link className="material-main-action" to="/studio/import">＋ 上传试卷或教材</Link>
    </section>
    <label className="materials-search">查找教材或课程<input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="输入名称、数学或英语" /></label>
    <nav className="materials-filters" aria-label="材料类型">{[["all", "全部"], ["books", "教材与课程"], ["papers", "试卷"], ["unknown", "待识别材料"]].map(([value, label]) => <button type="button" key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>)}</nav>
    {errors.map((message) => <div className="chapter-error" role="alert" key={message}><span>{message}</span><button type="button" onClick={() => void reload()}>重试</button></div>)}
    {loading && <p role="status">正在读取教材与课程…</p>}
    {!loading && count === 0 && <section className="chapter-panel materials-empty"><h2>{query || filter !== "all" ? "没有找到匹配的材料" : errors.length ? "部分内容暂时无法读取" : "从一本教材开始"}</h2><p>{query || filter !== "all" ? "试试其他名称，或切换材料类型。" : errors.length ? "请重试读取；已有文件与课程不会因此被删除。" : "上传 PDF 或图片，系统自动识别。文件和课程都会显示在这里。"}</p>{!query && filter === "all" && <Link to="/studio/import">上传第一份材料 →</Link>}</section>}
    <section className="materials-grid" aria-label="教材与课程列表">
      {courses.map((item) => <article className="chapter-panel material-card" key={`course-${item.chapterId}`}><span className="material-kind">{item.subject === "english" ? "英语阅读" : "数学课程"}</span><h2>{item.title}</h2><p>{statuses[item.status] ?? "待复核"} · {item.currentLessonIds.length} 节内容</p><Link className="material-main-action" to={`/studio/chapters/${item.chapterId}`}>{item.status === "published" ? "查看课程" : item.currentLessonIds.length ? "继续复核" : "制作课程"} →</Link>{item.publicationId && <Link to={`/learn/chapters/${item.chapterId}`}>查看学生版本</Link>}<MaterialDeleteAction name={item.title} onDelete={() => remove("course", item.chapterId)} /></article>)}
      {files.map((item) => <article className="chapter-panel material-card" key={`upload-${item.uploadId}`}><span className="material-kind">{item.materialKind === "paper" ? "已上传试卷" : item.materialKind === "textbook" ? "已上传教材" : "待识别材料"}</span><h2>{item.filename}</h2><p>{item.pageCount ?? "?"} 页 · {item.questionCount} 道已生成练习</p>{item.questionCount > 0 && <Link className="material-main-action" to={`/studio/import?uploadId=${encodeURIComponent(item.uploadId)}`}>查看练习 →</Link>}{item.materialKind !== "paper" && <Link to={`/studio/chapters/new?uploadId=${encodeURIComponent(item.uploadId)}`}>自动制作课程</Link>}<MaterialDeleteAction name={item.filename} onDelete={() => remove("upload", item.uploadId)} /></article>)}
    </section>
    <details className="materials-tools"><summary>教学工具</summary><nav aria-label="教学工具"><Link to="/studio/prompts">教学策略</Link><Link to="/studio/metrics">模型调用指标</Link><Link to="/studio/dependency-preflight">环境检查</Link></nav></details>
  </main>;
}
