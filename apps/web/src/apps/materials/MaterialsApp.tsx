import { useState } from "react";
import { Link } from "react-router";
import { useMaterials } from "./useMaterials";
import "../chapters/chapters.css";
import "./materials.css";

const statuses: Record<string, string> = { draft: "待生成", in_review: "待复核", needs_review: "需修改", published: "已发布" };

/** One library for uploaded sources and source-bound courses, without moving either record. */
export function MaterialsApp() {
  const { uploads, chapters, errors, loading, reload } = useMaterials();
  const [search, setSearch] = useState("");
  const query = search.trim().toLocaleLowerCase();
  const files = uploads.filter((item) => item.filename.toLocaleLowerCase().includes(query));
  const courses = chapters.filter((item) => `${item.title} ${item.subject === "english" ? "英语 阅读" : "数学"}`.toLocaleLowerCase().includes(query));
  const count = files.length + courses.length;
  return <main className="chapter-shell materials-shell">
    <header className="chapter-topbar"><Link to="/">← 返回首页</Link><strong>Dotty · 教材与课程</strong><button type="button" disabled={loading} onClick={() => void reload()}>刷新</button></header>
    <section className="materials-heading"><div><span className="eyebrow">教材与课程，一个地方</span><h1>我的教材</h1><p>选择教材继续编辑，或把教材页制作成数学课程和英语阅读。</p></div>
      <details className="materials-add"><summary>＋ 添加教材</summary><div><Link to="/studio/import">上传 PDF 或图片</Link><Link to="/studio/chapters/new">粘贴教材原文</Link></div></details>
    </section>
    <label className="materials-search">查找教材或课程<input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="输入名称、数学或英语" /></label>
    {errors.map((message) => <div className="chapter-error" role="alert" key={message}><span>{message}</span><button type="button" onClick={() => void reload()}>重试</button></div>)}
    {loading && <p role="status">正在读取教材与课程…</p>}
    {!loading && count === 0 && <section className="chapter-panel materials-empty"><h2>{query ? "没有找到匹配的教材" : errors.length ? "部分内容暂时无法读取" : "从一本教材开始"}</h2><p>{query ? "试试其他名称，或清空搜索。" : errors.length ? "请重试读取；已有文件与课程不会因此被删除。" : "上传 PDF，或粘贴一页数学、英语原文。创建后的课程会显示在这里。"}</p>{!query && <Link to="/studio/chapters/new">粘贴原文，制作第一节课 →</Link>}</section>}
    <section className="materials-grid" aria-label="教材与课程列表">
      {courses.map((item) => <article className="chapter-panel material-card" key={`course-${item.chapterId}`}><span className="material-kind">{item.subject === "english" ? "英语阅读" : "数学课程"}</span><h2>{item.title}</h2><p>{statuses[item.status] ?? "待复核"} · {item.currentLessonIds.length} 节内容</p><Link className="material-main-action" to={`/studio/chapters/${item.chapterId}`}>{item.status === "published" ? "查看课程" : item.currentLessonIds.length ? "继续复核" : "制作课程"} →</Link>{item.publicationId && <Link to={`/learn/chapters/${item.chapterId}`}>查看学生版本</Link>}</article>)}
      {files.map((item) => <article className="chapter-panel material-card" key={`upload-${item.uploadId}`}><span className="material-kind">已上传教材</span><h2>{item.filename}</h2><p>{item.pageCount ?? "?"} 页 · {item.questionCount} 道已生成练习</p><Link className="material-main-action" to={`/studio/import?uploadId=${encodeURIComponent(item.uploadId)}`}>查看练习 →</Link><Link to={`/studio/chapters/new?uploadId=${encodeURIComponent(item.uploadId)}`}>选取页码，制作课程</Link></article>)}
    </section>
    <details className="materials-tools"><summary>教学工具</summary><nav aria-label="教学工具"><Link to="/studio/prompts">教学策略</Link><Link to="/studio/metrics">模型调用指标</Link><Link to="/studio/dependency-preflight">环境检查</Link></nav></details>
  </main>;
}
