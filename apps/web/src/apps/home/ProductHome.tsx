import { useState } from "react";
import { useNavigate } from "react-router";
import { PRODUCT_TERMS } from "../../productTerms";

const HOME_ENTRY_KEY = "dotty-home-entry";
type HomeEntry = "student" | "studio" | "teacher";

function readLastEntry(): HomeEntry | null {
  try {
    const value = localStorage.getItem(HOME_ENTRY_KEY);
    return value === "student" || value === "studio" || value === "teacher" ? value : null;
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
  const enterTeacher = () => {
    rememberEntry("teacher");
    navigate("/teacher");
  };

  return (
    <main className="product-home">
      <header className="product-home-header">
        <div className="brand-mark">D</div>
        <div>
          <strong>Dotty Tutor</strong>
          <span>个人 AI 学习工具</span>
        </div>
        <span className="demo-badge">{PRODUCT_TERMS.demoBadge}</span>
      </header>

      <section className="product-home-hero">
        <h1>选择你的使用入口</h1>
        <p>学生进入学习空间完成练习与复习；教材和课程统一管理，复核后再发布给学生。</p>
      </section>

      <section className="product-entry-grid" aria-label="产品入口">
        <article className={`product-entry-card student${lastEntry === "student" ? " last-entry" : ""}`}>
          <div className="entry-card-heading">
            <span className="entry-index">01</span>
            <span className="entry-status">学生入口</span>
            {lastEntry === "student" && <span className="entry-last-badge">上次从这里进入</span>}
          </div>
          <h2>学生学习空间</h2>
          <p>直接进入已发布练习、个人错题本和复习任务，不需要上传整本教材或配置模型。</p>
          <ul>
            <li>已审核练习与分步讲解</li>
            <li>拍照录入与人工确认错题</li>
            <li>AI 错题辅导与掌握验证</li>
          </ul>
          <button onClick={enterStudent}>进入学生学习空间</button>
        </article>

        <article className={`product-entry-card producer${lastEntry === "studio" ? " last-entry" : ""}`}>
          <div className="entry-card-heading">
            <span className="entry-index">02</span>
            <span className="entry-status">内容生产</span>
            {lastEntry === "studio" && <span className="entry-last-badge">上次从这里进入</span>}
          </div>
          <h2>教材与课程</h2>
          <p>数学教材与英语阅读统一管理。从一本教材开始，制作课程、复核内容，再交给学生。</p>
          <ul>
            <li>数学与英语教材统一管理</li>
            <li>按教材页制作课程</li>
            <li>复核内容后分享给学生</li>
          </ul>
          <div className="producer-entry-actions">
            <button onClick={enterStudio}>打开我的教材</button>
          </div>
        </article>
        <article className={`product-entry-card teacher${lastEntry === "teacher" ? " last-entry" : ""}`}>
          <div className="entry-card-heading"><span className="entry-index">03</span><span className="entry-status">教师入口</span>{lastEntry === "teacher" && <span className="entry-last-badge">上次从这里进入</span>}</div>
          <h2>班级学习进展</h2>
          <p>创建班级、布置已发布练习，并按学生和知识点查看完成与掌握情况。</p>
          <ul><li>班级成员与作业管理</li><li>学生作业完成进度</li><li>知识点掌握分布看板</li></ul>
          <button onClick={enterTeacher}>进入教师工作台</button>
        </article>
      </section>
    </main>
  );
}
