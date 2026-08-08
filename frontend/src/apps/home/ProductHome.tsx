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
        <span className="eyebrow">CHOOSE A LEARNING PATH</span>
        <h1>选择你的学习入口</h1>
        <p>教材数字化与错题陪练共享题目、判题和掌握度能力，但保留各自清晰的学习流程。</p>
      </section>

      <section className="product-entry-grid" aria-label="学习入口">
        <article className="product-entry-card available">
          <div className="entry-card-heading">
            <span className="entry-index">01</span>
            <span className="entry-status">已可用</span>
          </div>
          <h2>教材互动学习</h2>
          <p>上传教材页或整本 PDF，自动识别题目、生成分步讲解并进行互动练习。</p>
          <ul>
            <li>PDF 与扫描教材结构化</li>
            <li>多题型交互与确定性判题</li>
            <li>分层提示、课程播放与语音</li>
          </ul>
          <button onClick={() => navigate("/textbooks")}>进入教材学习</button>
        </article>

        <article className="product-entry-card planned">
          <div className="entry-card-heading">
            <span className="entry-index">02</span>
            <span className="entry-status">录题已可用</span>
          </div>
          <h2>AI 错题陪练</h2>
          <p>拍下错题，修正确认题目和错误原因；后续由单题智能体持续追问、提示并验证掌握。</p>
          <ul>
            <li>错题归类到教材章节与知识点</li>
            <li>概念、审题、计算等错误诊断</li>
            <li>连续答对验证与复习计划</li>
          </ul>
          <button onClick={() => navigate("/mistakes")}>查看错题陪练</button>
        </article>
      </section>
    </main>
  );
}
