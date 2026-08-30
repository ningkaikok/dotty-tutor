import { useState } from "react";
import { useNavigate } from "react-router";

const HOME_ENTRY_KEY = "dotty-home-entry";
type HomeEntry = "student" | "studio";

function readLastEntry(): HomeEntry | null {
  try {
    const value = localStorage.getItem(HOME_ENTRY_KEY);
    return value === "student" || value === "studio" ? value : null;
  } catch {
    // 隐私模式或站点数据被禁用时 localStorage 访问会直接抛异常；当作“没有记录”
    // 处理即可，不能让首页因此白屏。
    return null;
  }
}

function rememberEntry(entry: HomeEntry) {
  try {
    localStorage.setItem(HOME_ENTRY_KEY, entry);
  } catch {
    // 同上：写入失败不影响本次导航，只是下次访问不会有记忆效果。
  }
}

export function ProductHome() {
  const navigate = useNavigate();
  // 这是本机 Demo，学生和内容生产者共用同一台机器：自动跳转会让首页变得
  // 难以再次到达，而“上次去过哪”只是本机 localStorage 信号，够不上强制
  // 导航的门槛。因此这里只把上次的入口提升为主视觉（加强调、加小标记），
  // 从不自动跳转；另一张卡必须保持完全可用。
  const [lastEntry] = useState<HomeEntry | null>(() => readLastEntry());

  const enterStudent = () => {
    rememberEntry("student");
    navigate("/learn");
  };
  const enterStudio = () => {
    rememberEntry("studio");
    navigate("/studio");
  };

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
        <article className={`product-entry-card student${lastEntry === "student" ? " last-entry" : ""}`}>
          <div className="entry-card-heading">
            <span className="entry-index">01</span>
            <span className="entry-status">学生入口</span>
            {lastEntry === "student" && <span className="entry-last-badge">上次从这里进入</span>}
          </div>
          <h2>学生学习空间</h2>
          <p>直接进入已发布互动试卷、个人错题本和复习任务，不需要上传整本教材或配置模型。</p>
          <ul>
            <li>已审核互动试卷与分步讲解</li>
            <li>拍照录入与人工确认错题</li>
            <li>AI 错题陪练与掌握验证</li>
          </ul>
          <button onClick={enterStudent}>进入学生学习空间</button>
        </article>

        <article className={`product-entry-card producer${lastEntry === "studio" ? " last-entry" : ""}`}>
          <div className="entry-card-heading">
            <span className="entry-index">02</span>
            <span className="entry-status">内容生产</span>
            {lastEntry === "studio" && <span className="entry-last-badge">上次从这里进入</span>}
          </div>
          <h2>内容生产工作台</h2>
          <p>上传教材页或整本 PDF，完成 OCR、题目生成、质量复核和互动内容预览。</p>
          <ul>
            <li>PDF 与扫描教材结构化</li>
            <li>生成模型和 OCR 运行时配置</li>
            <li>多题型交互与分步讲解预览</li>
          </ul>
          <button onClick={enterStudio}>进入内容生产工作台</button>
        </article>
      </section>
    </main>
  );
}
