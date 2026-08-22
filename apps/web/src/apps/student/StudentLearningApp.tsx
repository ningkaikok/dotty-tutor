import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { loadPublishedPublications } from "../../api/publications";
import type { PublicationSummary } from "../../types/index";
import "./student.css";

/**
 * Student-facing navigation shell.
 *
 * The page intentionally owns no upload, OCR, or model settings. Those
 * production concerns live under /studio; students only consume published
 * material and open their personal practice records here.
 */
export function StudentLearningApp() {
  const navigate = useNavigate();
  const [publications, setPublications] = useState<PublicationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    loadPublishedPublications()
      .then(setPublications)
      .catch((requestError) => setError(requestError instanceof Error ? requestError.message : "试卷目录加载失败"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="student-shell">
      <header className="student-header">
        <button className="route-back-button" onClick={() => navigate("/")}>← 返回入口</button>
        <div className="brand-mark">D</div>
        <div>
          <strong>Dotty</strong>
          <span>学生学习空间</span>
        </div>
        <span className="demo-badge">STUDENT DEMO</span>
      </header>

      <section className="student-hero">
        <span className="eyebrow">LEARN · PRACTICE · MASTER</span>
        <h1>直接开始学习</h1>
        <p>这里没有教材上传、OCR 或模型配置。学生只需完成互动练习、订正错题并按计划复习。</p>
      </section>

      <section className="student-action-grid" aria-label="学生学习功能">
        <article className="student-action-card paper-card">
          <div className="student-card-heading">
            <span className="student-card-icon" aria-hidden="true">卷</span>
            <span className="student-card-status">已可用</span>
          </div>
          <h2>互动试卷</h2>
          <p>查看内容生产端审核并发布的互动试卷，完成题目、分层提示与讲解。</p>
          {loading && <div className="student-empty-note">正在加载已发布试卷…</div>}
          {!loading && !publications.length && <div className="student-empty-note">暂无已发布试卷，请先在内容生产端发布。</div>}
          {error && <div className="student-empty-note">{error}</div>}
          <div className="student-paper-list">
            {publications.map((paper) => (
              <button key={paper.publicationId} onClick={() => navigate(`/learn/papers/${paper.publicationId}`)}>
                <strong>{paper.title}</strong>
                <span>{paper.lessonCount} 道题 · 继续学习 →</span>
              </button>
            ))}
          </div>
        </article>

        <article className="student-action-card mistake-card">
          <div className="student-card-heading">
            <span className="student-card-icon" aria-hidden="true">错</span>
            <span className="student-card-status">已可用</span>
          </div>
          <h2>我的错题本</h2>
          <p>互动试卷中的错题会自动记录；纸质作业仍可拍照补录，再通过多轮陪练找到真正卡点。</p>
          <ul>
            <li>在线错题自动收集</li>
            <li>纸质错题拍照补录</li>
            <li>一题一线程的分步陪练</li>
          </ul>
          <button onClick={() => navigate("/mistakes")}>打开我的错题本</button>
        </article>

        <article className="student-action-card review-card">
          <div className="student-card-heading">
            <span className="student-card-icon" aria-hidden="true">复</span>
            <span className="student-card-status">已可用</span>
          </div>
          <h2>掌握与复习</h2>
          <p>查看错题掌握率、待复习任务和知识点进度，按 1、3、7 天节奏巩固。</p>
          <ul>
            <li>今日复习任务</li>
            <li>进阶本与验证记录</li>
            <li>知识点学习进度</li>
          </ul>
          <button onClick={() => navigate("/mistakes/progress")}>查看学习进度</button>
        </article>
      </section>

      <aside className="student-boundary-note">
        <strong>为什么这里不能上传 PDF？</strong>
        <span>教材识别和题目生成属于内容生产流程。学生端只展示已审核、可作答的内容，避免把复杂配置暴露给学习者。</span>
      </aside>
    </main>
  );
}
