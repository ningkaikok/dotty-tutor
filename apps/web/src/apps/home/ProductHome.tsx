import { useNavigate } from "react-router";

export function ProductHome() {
  const navigate = useNavigate();

  return (
    <main className="product-home">
      <header className="product-home-header">
        <div className="brand-mark">D</div>
        <div>
          <strong>Dotty Tutor</strong>
          <span>个人 AI 学习工具</span>
        </div>
        <span className="demo-badge">LOCAL DEMO</span>
      </header>

      <section className="product-home-hero">
        <h1>选择你的使用入口</h1>
        <p>学生直接完成已发布试卷、订正错题和复习；教材上传、OCR 与互动内容生成集中在内容生产工作台。</p>
      </section>

      <section className="product-entry-grid" aria-label="产品入口">
        <article className="product-entry-card student">
          <div className="entry-card-heading">
            <span className="entry-index">01</span>
            <span className="entry-status">学生入口</span>
          </div>
          <h2>学生学习空间</h2>
          <p>直接进入已发布互动试卷、个人错题本和复习任务，不需要上传整本教材或配置模型。</p>
          <ul>
            <li>已审核互动试卷与分步讲解</li>
            <li>拍照录入与人工确认错题</li>
            <li>AI 错题陪练与掌握验证</li>
          </ul>
          <button onClick={() => navigate("/learn")}>进入学生学习空间</button>
        </article>

        <article className="product-entry-card producer">
          <div className="entry-card-heading">
            <span className="entry-index">02</span>
            <span className="entry-status">内容生产</span>
          </div>
          <h2>内容生产工作台</h2>
          <p>上传教材页或整本 PDF，完成 OCR、题目生成、质量复核和互动内容预览。</p>
          <ul>
            <li>PDF 与扫描教材结构化</li>
            <li>生成模型和 OCR 运行时配置</li>
            <li>多题型交互与分步讲解预览</li>
          </ul>
          <button onClick={() => navigate("/studio")}>进入内容生产工作台</button>
        </article>
      </section>
    </main>
  );
}
